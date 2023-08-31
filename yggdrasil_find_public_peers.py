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

_log_format = f"%(name)s [%(asctime)s] %(message)s"

def get_logger(name : str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG)
    stream_handler.setFormatter(logging.Formatter(_log_format))
    logger.addHandler(stream_handler)
    return logger

logger = get_logger("YFPP")

@dataclass(frozen=True)
class Settings:
    parallel : int = 0
    pings : int = 0
    best_count : int = 0
    ping_interval : float = 0
    rewrite_config_peers : bool = False
    yggdrasil_peers_json : str = ""

@dataclass(frozen=True)
class PeerData:
    address : str
    url : str
    world_part : str
    country : str

    def __str__(self) -> str:
        s = f"{self.address} ({self.world_part}/{self.country})"
        return s

@dataclass(frozen=False)
class PeerStatistic:
    peer : PeerData
    packet_loss : int = -1
    rtt_min : float = 0.0
    rtt_avg : float = 0.0
    rtt_max : float = 0.0
    rtt_mdev : float = 0.0
    error : int = 0

    def __lt__(self, other) -> bool:
        lesser = (self.rtt_avg < other.rtt_avg)
        return lesser

    def __str__(self) -> str:
        s = f"{self.peer}"
        if (self.packet_loss >= 0):
            s += f" loss={self.packet_loss} min={self.rtt_min} avg={self.rtt_avg} max={self.rtt_max} mdev={self.rtt_mdev}"
        return s

    def ping_success(self) -> bool:
        return (self.error == 0)

    def parse_ping_output(self, ping_output : str) -> None:
        m_rtt = re.search(r"\s*rtt min\/avg\/max\/mdev = (\d[\d.]+)\/(\d[\d.]+)\/(\d[\d.]+)\/(\d[\d.]+)\s*ms", ping_output)
        m_loss = re.search(r"(\d+)% packet loss", ping_output)
        if (not (m_rtt and m_loss)):
            logger.debug(f"cant parse ping output: {ping_output}")
            self.error = -1
            return
        self.rtt_min = float(m_rtt.group(1))
        self.rtt_avg = float(m_rtt.group(2))
        self.rtt_max = float(m_rtt.group(3))
        self.rtt_mdev = float(m_rtt.group(4))
        self.packet_loss = int(m_loss.group(1))
        return

@dataclass(frozen=True)
class ProcessingPeer:
    peer_statistic : PeerStatistic
    process : subprocess.Popen

def parse_md_line(s : str) -> list[str] | None:
    m = re.search(r"\s*\* `(tls:\/\/|tcp:\/\/)([\d\.\[\]a-zA-Z:-]+)(:\d+.*)`", s)
    if (m):
        url = m.group(1) + m.group(2) + m.group(3)
        address = m.group(2).strip("[]")
        return [url, address]
    return None

def parse_md(filename : str, word_part : str, country : str) -> list[PeerData]:
    peers : list[PeerData] = []
    with open(filename, 'r', encoding='UTF-8') as file:
        for line in file:
            s = line.rstrip()
            ua = parse_md_line(s)
            if (ua):
                url, address = ua
                peers.append(PeerData(address, url, word_part, country))
    return peers

def get_peers_from_json(json_filename : str) -> list[PeerData]:
    peers : list[PeerData] = []
    if (len(json_filename) == 0):
        return peers
    with open(json_filename, "r") as file:
        json_data = json.load(file)
    peers_data = json_data["yggdrasil_peers"]
    for peer_data in peers_data:
        peer = PeerData(address=peer_data["address"], url=peer_data["url"], \
                        world_part=peer_data["world_part"], country=peer_data["country"])
        peers.append(peer)
    return peers

def save_peers_to_json(json_filename : str, peers : list[PeerData]) -> None:
    if (len(json_filename) == 0):
        return
    peers_data = []
    for peer in peers:
        peer_data = {"address" : peer.address, "url" : peer.url, \
                     "world_part" : peer.world_part, "country" : peer.country}
        peers_data.append(peer_data)
    json_data  = {"yggdrasil_peers" : peers_data}
    with open(json_filename, "w") as file:
        json.dump(json_data, file)
    return

def get_peers_from_git() -> list[PeerData]:
    peers : list[PeerData] = []
    commands = f'git clone --quiet --depth 1 "https://github.com/yggdrasil-network/public-peers"'
    current_dir = os.getcwd()
    os.chdir("/tmp")
    process = subprocess.Popen(commands, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    process.wait()
    if (process.poll() != 0):
        logger.info("git clone error")
        return peers
    os.chdir("public-peers")
    directories = ["africa", "asia", "europe", "mena", "north-america", "south-america"]
    for directory in directories:
        os.chdir(directory)
        files = os.listdir()
        for filename in files:
            logger.debug(f"found {directory}/{filename}")
            if (filename[-3:] == ".md"):
                peers += parse_md(filename, directory, filename[:-3])
        os.chdir("..")
    os.chdir("..")
    shutil.rmtree("public-peers")
    os.chdir(current_dir)
    logger.info(f"got {len(peers)} public peers")
    return peers

def ping_peers(peers : list[PeerData], settings : Settings) -> list[PeerStatistic]:
    ping_statistic = [PeerStatistic(peer=p) for p in peers]
    ping_waiting = ping_statistic.copy()
    if (logger.getEffectiveLevel() == logging.INFO):
        pbar = tqdm(total=len(ping_waiting))
    processing : list[ProcessingPeer] = []
    while ((len(ping_waiting) != 0) or (len(processing) != 0)):
        if ((len(processing) < settings.parallel) and (len(ping_waiting) != 0)):
            peer_stat = ping_waiting.pop()
            logger.debug(f"ping start {peer_stat}")
            commands = f'ping -c {settings.pings} -q -i {settings.ping_interval} "{peer_stat.peer.address}" 2> /dev/null'
            process = subprocess.Popen(commands, shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            processing.append(ProcessingPeer(peer_stat, process))
        time.sleep(0.1)
        processing_working : list[ProcessingPeer] = []
        for p in processing:
            process = p.process
            peer_stat = p.peer_statistic
            poll = process.poll()
            if (poll is None):
                processing_working.append(p)
            elif (poll != 0):
                logger.debug(f"ping done {peer_stat} with error")
                peer_stat.error = poll
                if (logger.getEffectiveLevel() == logging.INFO):
                    pbar.update()
            else:
                output_str = process.communicate()[0].decode("utf-8")
                peer_stat.parse_ping_output(output_str)
                logger.debug(f"ping done {peer_stat}")
                if (logger.getEffectiveLevel() == logging.INFO):
                    pbar.update()
        processing = processing_working
    if (logger.getEffectiveLevel() == logging.INFO):
        pbar.close()
    return ping_statistic

def best_peers(ping_statistics : list[PeerStatistic], best : int) -> list[PeerStatistic]:
    best_peers = [ping_statistic for ping_statistic in ping_statistics if ping_statistic.ping_success()]
    logger.info(f"success ping {len(best_peers)} peers")
    best_peers.sort()
    best_peers = best_peers[0:best]
    logger.debug(f"found {len(best_peers)} best pings")
    for best_peer in best_peers:
        logger.debug(f"  {best_peer}")
    return [p.peer for p in best_peers]

def find_public_peers(settings : Settings) -> list[PeerData]:
    logger.info(f"find public peers with {settings}")
    peers = get_peers_from_git()
    if (len(peers) == 0):
        peers = get_peers_from_json(settings.yggdrasil_peers_json)
    else:
        save_peers_to_json(settings.yggdrasil_peers_json, peers)
    if (len(peers) != 0):
        ping_statistic = ping_peers(peers, settings)
        peers = best_peers(ping_statistic, settings.best_count)
    return peers

def yggdrasil_conf_has_peers(yggdrasil_conf_filename : str) -> bool:
    with open(yggdrasil_conf_filename, "r") as file:
        conf = hjson.load(file)
    peers = conf.get("Peers", [])
    return (len(peers) != 0)

def save_to_yggdrasil_conf(yggdrasil_conf_filename : str, peers : list[PeerData]) -> None:
    with open(yggdrasil_conf_filename, "r") as file:
        conf = hjson.load(file)

    conf["Peers"] : list[str] = []
    for peer in peers:
        conf["Peers"].append(peer.url)

    with open(yggdrasil_conf_filename, "w") as file:
        hjson.dump(conf, file)
    return

def get_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Find yggdrasil public peers')
    parser.add_argument('--parallel', dest='parallel', metavar='PARALLEL', \
        type=int, default=10, help='Number of parallel ping processes')
    parser.add_argument('--pings', dest='pings', metavar='PINGS', \
        type=int, default=5, help='Number of ping packets for one peer')
    parser.add_argument('--best', dest='best', metavar='BEST', \
        type=int, default=5, help='Number of best peers to choose')
    parser.add_argument('--ping-interval', dest='ping_interval', metavar='PING_INTERVAL', \
        type=float, default=0.1, help='Interval betveen pings for one peer in seconds')
    parser.add_argument("-v", dest='verbose', help="Print extra logs",
        action="store_true")
    parser.add_argument("-q", dest='quiet', help="Print minimum logs",
        action="store_true")
    parser.add_argument("--rewrite-config-peers", dest='rewrite_config_peers', help="Rewrite existing peers in config",
        action="store_true")
    parser.add_argument('--yggdrasil-conf', dest='yggdrasil_conf', metavar='YGGDRASIL_CONF', \
        type=str, default="yggdrasil.conf", help='Save best peers to existing yggdrasil configuration file')
    parser.add_argument('--yggdrasil-peers-json', dest='yggdrasil_peers_json', metavar='YGGDRASIL_PEERS_JSON', \
        type=str, default="", help='Save all peers from git to file')
    return parser.parse_args()

def set_logger_level(args : argparse.Namespace) -> None:
    if (args.quiet):
        logger.setLevel(logging.WARNING)
    else:
        if (args.verbose):
            logger.setLevel(logging.DEBUG)
        else:
            logger.setLevel(logging.INFO)
    return

def main() -> None:
    args = get_arguments()

    set_logger_level(args)

    settings = Settings(parallel=args.parallel, pings=args.pings, \
                        best_count=args.best, ping_interval=args.ping_interval, \
                        rewrite_config_peers=args.rewrite_config_peers, \
                        yggdrasil_peers_json=args.yggdrasil_peers_json)
    if (len(args.yggdrasil_conf) != 0):
        if (not settings.rewrite_config_peers):
            if (yggdrasil_conf_has_peers(args.yggdrasil_conf)):
                logger.warning(f"config {args.yggdrasil_conf} already has not empty public peers list")
                sys.exit(0)
    peers = find_public_peers(settings)
    if (len(peers) == 0):
        logger.info(f"peers not found")
        sys.exit(1)
    else:
        logger.info(f"best peers:")
        for peer in peers:
            logger.info(f"  {peer}")
        if (len(args.yggdrasil_conf) != 0):
            try:
                save_to_yggdrasil_conf(args.yggdrasil_conf, peers)
            except:
                sys.exit(1)
    sys.exit(0)

if __name__ == '__main__':
    main()