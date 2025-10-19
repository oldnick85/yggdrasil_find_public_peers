"""
Yggdrasil Public Peers Finder (YFPP)

A utility to discover, test, and select the best Yggdrasil network public peers
based on latency and packet loss metrics. The tool can automatically update
Yggdrasil configuration files with the best performing peers.

Features:
- Fetches public peers from Yggdrasil's GitHub repository
- Tests peers with parallel ping operations
- Selects best peers based on latency and country distribution
- Updates Yggdrasil configuration automatically
- Supports custom repositories and various configuration options

Copyright (c) 2025 [oldnick85]
License: MIT
"""

import subprocess
import logging
import time
import os
import shutil
import sys
from dataclasses import dataclass
from tqdm import tqdm
import hjson
import json
import argparse
import re

# Logging configuration
_log_format = f"%(name)s [%(asctime)s] %(message)s"

def get_logger(name: str) -> logging.Logger:
    """Create and configure a logger instance."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG)
    stream_handler.setFormatter(logging.Formatter(_log_format))
    logger.addHandler(stream_handler)
    return logger

# Global logger instance
logger = get_logger("YFPP")

@dataclass(frozen=True)
class Settings:
    """Configuration settings for the peer discovery process."""
    force: bool = False
    quiet: bool = False
    verbose: bool = False
    parallel: int = 0
    pings: int = 0
    best_count: int = 0
    max_from_country: int = 0
    ping_interval: float = 0
    rewrite_config_peers: bool = False
    yggdrasil_peers_json: str = ""
    yggdrasil_conf: str = ""
    repo_url: str = "https://github.com/yggdrasil-network/public-peers"

# Global settings instance
settings = Settings()

@dataclass(frozen=True)
class PeerData:
    """Represents a Yggdrasil peer with location information."""
    address: str
    url: str
    world_part: str
    country: str

    def __str__(self) -> str:
        return f"{self.address} ({self.world_part}/{self.country})"

@dataclass(frozen=False)
class PeerStatistic:
    """Stores ping statistics for a peer."""
    peer: PeerData
    packet_loss: int = -1
    rtt_min: float = 0.0
    rtt_avg: float = 0.0
    rtt_max: float = 0.0
    rtt_mdev: float = 0.0
    error: int = 0

    def __lt__(self, other) -> bool:
        """Compare peers based on average RTT for sorting."""
        return self.rtt_avg < other.rtt_avg

    def __str__(self) -> str:
        s = f"{self.peer}"
        if (self.packet_loss >= 0):
            s += f" loss={self.packet_loss} min={self.rtt_min} avg={self.rtt_avg} max={self.rtt_max} mdev={self.rtt_mdev}"
        return s

    def ping_success(self) -> bool:
        """Check if ping was successful."""
        return self.error == 0

    def parse_ping_output(self, ping_output: str) -> None:
        """Parse Linux ping command output to extract statistics."""
        m_rtt = re.search(r"\s*rtt min\/avg\/max\/mdev = (\d[\d.]+)\/(\d[\d.]+)\/(\d[\d.]+)\/(\d[\d.]+)\s*ms", ping_output)
        m_loss = re.search(r"(\d+)% packet loss", ping_output)
        
        if not (m_rtt and m_loss):
            logger.debug(f"Can't parse ping output: {ping_output}")
            self.error = -1
            return
            
        self.rtt_min = float(m_rtt.group(1))
        self.rtt_avg = float(m_rtt.group(2))
        self.rtt_max = float(m_rtt.group(3))
        self.rtt_mdev = float(m_rtt.group(4))
        self.packet_loss = int(m_loss.group(1))

@dataclass(frozen=True)
class ProcessingPeer:
    """Tracks a peer and its associated ping process."""
    peer_statistic: PeerStatistic
    process: subprocess.Popen

@dataclass(frozen=True)
class UrlAddress:
    """Stores URL and address components of a peer."""
    url: str
    address: str

def parse_md_line(s: str) -> UrlAddress | None:
    """
    Parse a markdown line to extract peer URL and address.
    
    Args:
        s: Markdown line containing peer information
        
    Returns:
        UrlAddress object or None if parsing fails
    """
    m = re.search(r"\s*\* `(tls:\/\/|tcp:\/\/)([\d\.\[\]a-zA-Z:-]+)(:\d+.*)`", s)
    if m:
        url = m.group(1) + m.group(2) + m.group(3)
        address = m.group(2).strip("[]")
        return UrlAddress(url=url, address=address)
    return None

def parse_md(filename: str, world_part: str, country: str) -> list[PeerData]:
    """
    Parse markdown file to extract peer information.
    
    Args:
        filename: Path to markdown file
        world_part: Geographic region (e.g., 'europe', 'asia')
        country: Country code
        
    Returns:
        List of PeerData objects
    """
    peers: list[PeerData] = []
    with open(filename, 'r', encoding='UTF-8') as file:
        for line in file:
            ua = parse_md_line(line.rstrip())
            if ua is not None:
                peers.append(PeerData(ua.address, ua.url, world_part, country))
    return peers

def get_peers_from_json(json_filename: str) -> list[PeerData]:
    """
    Load peers from JSON cache file.
    
    Args:
        json_filename: Path to JSON file
        
    Returns:
        List of PeerData objects
    """
    peers: list[PeerData] = []
    if not json_filename:
        return peers
        
    try:
        with open(json_filename, "r") as file:
            json_data = json.load(file)
        peers_data = json_data["yggdrasil_peers"]
        for peer_data in peers_data:
            peer = PeerData(
                address=peer_data["address"],
                url=peer_data["url"],
                world_part=peer_data["world_part"],
                country=peer_data["country"]
            )
            peers.append(peer)
    except Exception as e:
        logger.debug(f"Error loading peers from JSON: {e}")
        
    return peers

def save_peers_to_json(json_filename: str, peers: list[PeerData]) -> None:
    """
    Save peers to JSON cache file.
    
    Args:
        json_filename: Path to JSON file
        peers: List of PeerData objects to save
    """
    if not json_filename:
        return
        
    try:
        peers_data = []
        for peer in peers:
            peer_data = {
                "address": peer.address,
                "url": peer.url,
                "world_part": peer.world_part,
                "country": peer.country
            }
            peers_data.append(peer_data)
            
        json_data = {"yggdrasil_peers": peers_data}
        with open(json_filename, "w") as file:
            json.dump(json_data, file, indent=2)
    except Exception as e:
        logger.debug(f"Error saving peers to JSON: {e}")

def get_peers_from_git() -> list[PeerData]:
    """
    Fetch peers from Yggdrasil's public peers GitHub repository.
    
    Returns:
        List of PeerData objects from the repository
    """
    peers: list[PeerData] = []
    current_dir = os.getcwd()
    
    try:
        # Clone repository to temporary location
        os.chdir("/tmp")
        process = subprocess.Popen(
            f'git clone --quiet --depth 1 "{settings.repo_url}"',
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        process.wait()
        
        if process.poll() != 0:
            logger.error("Git clone failed")
            return peers
            
        # Parse peer files from all regional directories
        os.chdir("public-peers")
        directories = ["africa", "asia", "europe", "mena", "north-america", "south-america"]
        
        for directory in directories:
            if os.path.exists(directory):
                os.chdir(directory)
                files = os.listdir()
                for filename in files:
                    if filename.endswith(".md"):
                        logger.debug(f"Found {directory}/{filename}")
                        country = filename[:-3]  # Remove .md extension
                        peers += parse_md(filename, directory, country)
                os.chdir("..")
                
        logger.info(f"Retrieved {len(peers)} public peers from repository")
        
    except Exception as e:
        logger.error(f"Error fetching peers from git: {e}")
    finally:
        # Cleanup
        os.chdir("/tmp")
        if os.path.exists("public-peers"):
            shutil.rmtree("public-peers")
        os.chdir(current_dir)
        
    return peers

def ping_peers(peers: list[PeerData], settings: Settings) -> list[PeerStatistic]:
    """
    Ping all peers in parallel to gather latency statistics.
    
    Args:
        peers: List of peers to ping
        settings: Configuration settings
        
    Returns:
        List of PeerStatistic objects with ping results
    """
    ping_statistic = [PeerStatistic(peer=p) for p in peers]
    ping_waiting = ping_statistic.copy()
    
    # Progress bar for verbose mode
    pbar = tqdm(total=len(ping_waiting)) if logger.getEffectiveLevel() == logging.INFO else None
    processing: list[ProcessingPeer] = []
    
    while ping_waiting or processing:
        # Start new ping processes if we have capacity
        if len(processing) < settings.parallel and ping_waiting:
            peer_stat = ping_waiting.pop()
            logger.debug(f"Pinging {peer_stat}")
            
            commands = f'ping -c {settings.pings} -q -i {settings.ping_interval} "{peer_stat.peer.address}" 2> /dev/null'
            process = subprocess.Popen(
                commands,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL
            )
            processing.append(ProcessingPeer(peer_stat, process))
            
        time.sleep(0.1)  # Small delay to prevent CPU spinning
        
        # Check for completed processes
        processing_working: list[ProcessingPeer] = []
        for p in processing:
            process = p.process
            peer_stat = p.peer_statistic
            poll = process.poll()
            
            if poll is None:
                # Process still running
                processing_working.append(p)
            elif poll != 0:
                # Process failed
                logger.debug(f"Ping failed for {peer_stat} with error code {poll}")
                peer_stat.error = poll
                if pbar:
                    pbar.update()
            else:
                # Process completed successfully
                output_str = process.communicate()[0].decode("utf-8")
                peer_stat.parse_ping_output(output_str)
                logger.debug(f"Ping completed: {peer_stat}")
                if pbar:
                    pbar.update()
                    
        processing = processing_working
        
    if pbar:
        pbar.close()
        
    return ping_statistic

def best_peers(ping_statistics: list[PeerStatistic], best: int, max_from_country: int) -> list[PeerData]:
    """
    Select the best peers based on latency and country distribution.
    
    Args:
        ping_statistics: List of ping results
        best: Number of best peers to select
        max_from_country: Maximum peers from any single country
        
    Returns:
        List of best PeerData objects
    """
    # Filter successful pings and sort by average RTT
    successful_peers = [ps for ps in ping_statistics if ps.ping_success()]
    logger.info(f"Successful pings: {len(successful_peers)} peers")
    
    successful_peers.sort()
    
    if max_from_country <= 0:
        # Simple selection - just take the best N peers
        best_peers_list = successful_peers[:best]
    else:
        # Country-aware selection - limit peers from any single country
        countries: dict[str, int] = {}
        best_peers_list: list[PeerStatistic] = []
        
        for peer in successful_peers:
            country = peer.peer.country
            if country in countries:
                if countries[country] >= max_from_country:
                    continue  # Skip if country limit reached
                countries[country] += 1
            else:
                countries[country] = 1
                
            best_peers_list.append(peer)
            if len(best_peers_list) >= best:
                break
                
    logger.debug(f"Selected {len(best_peers_list)} best peers")
    for best_peer in best_peers_list:
        logger.debug(f"  {best_peer}")
        
    return [p.peer for p in best_peers_list]

def get_all_public_peers(settings: Settings) -> list[PeerData]:
    """
    Retrieve all public peers from git repository or cache.
    
    Args:
        settings: Configuration settings
        
    Returns:
        List of all available PeerData objects
    """
    logger.info("Fetching public peers")
    
    # Try to get peers from git repository first
    peers = get_peers_from_git()
    
    if not peers:
        # Fall back to cached JSON file
        logger.info("Falling back to cached peers JSON")
        peers = get_peers_from_json(settings.yggdrasil_peers_json)
    else:
        # Save freshly fetched peers to cache
        save_peers_to_json(settings.yggdrasil_peers_json, peers)
        
    return peers

def find_best_public_peers(settings: Settings, peers: list[PeerData]) -> list[PeerData]:
    """
    Find the best performing peers through ping testing.
    
    Args:
        settings: Configuration settings
        peers: List of peers to test
        
    Returns:
        List of best performing PeerData objects
    """
    logger.info("Finding best public peers through ping testing")
    
    if not peers:
        logger.warning("No peers available for testing")
        return []
        
    ping_statistic = ping_peers(peers, settings)
    best_peers_list = best_peers(ping_statistic, settings.best_count, settings.max_from_country)
    
    return best_peers_list

def yggdrasil_conf_has_peers(yggdrasil_conf_filename: str) -> bool:
    """
    Check if Yggdrasil config file already contains peers.
    
    Args:
        yggdrasil_conf_filename: Path to Yggdrasil config file
        
    Returns:
        True if config has peers, False otherwise
    """
    try:
        with open(yggdrasil_conf_filename, "r") as file:
            conf = hjson.load(file)
        peers = conf.get("Peers", [])
        return len(peers) > 0
    except Exception as e:
        logger.warning(f"Cannot read config file {yggdrasil_conf_filename}: {e}")
        return False

def save_to_yggdrasil_conf(yggdrasil_conf_filename: str, peers: list[PeerData]) -> None:
    """
    Save selected peers to Yggdrasil configuration file.
    
    Args:
        yggdrasil_conf_filename: Path to Yggdrasil config file
        peers: List of PeerData objects to save
    """
    if not yggdrasil_conf_filename:
        return
        
    try:
        with open(yggdrasil_conf_filename, "r") as file:
            conf = hjson.load(file)
            
        # Replace peers list
        conf["Peers"] = [peer.url for peer in peers]
        
        with open(yggdrasil_conf_filename, "w") as file:
            hjson.dump(conf, file)
            
        logger.info(f"Updated {len(peers)} peers in {yggdrasil_conf_filename}")
    except Exception as e:
        logger.error(f"Failed to update config file {yggdrasil_conf_filename}: {e}")
        raise

def get_arguments() -> argparse.Namespace:
    """Parse and return command line arguments."""
    parser = argparse.ArgumentParser(description='Find and select best Yggdrasil public peers')
    
    parser.add_argument('--parallel', dest='parallel', metavar='PARALLEL',
        type=int, default=10, help='Number of parallel ping processes (default: 10)')
    parser.add_argument('--pings', dest='pings', metavar='PINGS',
        type=int, default=5, help='Number of ping packets per peer (default: 5)')
    parser.add_argument('--best', dest='best', metavar='BEST',
        type=int, default=5, help='Number of best peers to select (default: 5)')
    parser.add_argument('--max-from-country', dest='max_from_country', metavar='MAX_COUNTRY',
        type=int, default=0, help='Maximum peers from any single country (default: 0 = unlimited)')
    parser.add_argument('--ping-interval', dest='ping_interval', metavar='PING_INTERVAL',
        type=float, default=0.1, help='Interval between pings in seconds (default: 0.1)')
    
    parser.add_argument("-v", dest='verbose', help="Enable verbose logging",
        action="store_true")
    parser.add_argument("-q", dest='quiet', help="Enable quiet mode (minimal logging)",
        action="store_true")
    parser.add_argument("--rewrite-config-peers", dest='rewrite_config_peers',
        help="Overwrite existing peers in config file", action="store_true")
    parser.add_argument("--force", dest='force', help="Run even if config has existing peers",
        action="store_true")
        
    parser.add_argument('--yggdrasil-conf', dest='yggdrasil_conf', metavar='YGGDRASIL_CONF',
        type=str, default="yggdrasil.conf", help='Yggdrasil config file path (default: yggdrasil.conf)')
    parser.add_argument('--yggdrasil-peers-json', dest='yggdrasil_peers_json', metavar='YGGDRASIL_PEERS_JSON',
        type=str, default="", help='JSON file to cache peers data')
    parser.add_argument('--repo-url', dest='repo_url', metavar='REPO_URL',
        type=str, default="https://github.com/yggdrasil-network/public-peers",
        help='Custom repository URL for peers list')
        
    return parser.parse_args()

def validate_settings(args: argparse.Namespace) -> bool:
    """
    Validate settings and check file permissions.
    
    Args:
        args: Parsed command line arguments
        
    Returns:
        True if settings are valid, False otherwise
    """
    global settings
    settings = Settings(
        force=args.force,
        quiet=args.quiet,
        verbose=args.verbose,
        parallel=args.parallel,
        pings=args.pings,
        best_count=args.best,
        max_from_country=args.max_from_country,
        ping_interval=args.ping_interval,
        rewrite_config_peers=args.rewrite_config_peers,
        yggdrasil_peers_json=args.yggdrasil_peers_json,
        yggdrasil_conf=args.yggdrasil_conf,
        repo_url=args.repo_url
    )
    
    # Check config file accessibility
    if settings.yggdrasil_conf and not settings.force:
        if not os.access(settings.yggdrasil_conf, os.W_OK):
            logger.error(f"Cannot write to config file: {settings.yggdrasil_conf}")
            return False
            
    return True

def set_logger_level() -> None:
    """Set appropriate logging level based on settings."""
    if settings.quiet:
        logger.setLevel(logging.WARNING)
    elif settings.verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

def main() -> None:
    """Main application entry point."""
    args = get_arguments()
    
    if not validate_settings(args):
        sys.exit(1)
        
    set_logger_level()
    
    # Check if we should proceed with existing config
    if settings.yggdrasil_conf and not settings.rewrite_config_peers:
        if yggdrasil_conf_has_peers(settings.yggdrasil_conf) and not settings.force:
            logger.warning(f"Config {settings.yggdrasil_conf} already has peers. Use --force to overwrite.")
            sys.exit(0)
    
    # Get and test peers
    peers = get_all_public_peers(settings)
    best_peers_list = find_best_public_peers(settings, peers)
    
    if not best_peers_list:
        logger.error("No suitable peers found")
        sys.exit(1)
        
    # Display results and update config
    logger.info("Best peers selected:")
    for peer in best_peers_list:
        logger.info(f"  {peer}")
        
    try:
        if settings.yggdrasil_conf:
            save_to_yggdrasil_conf(settings.yggdrasil_conf, best_peers_list)
    except Exception as e:
        logger.error(f"Failed to save configuration: {e}")
        sys.exit(1)
        
    logger.info("Configuration updated successfully")
    sys.exit(0)

if __name__ == '__main__':
    main()