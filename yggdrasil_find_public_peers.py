import subprocess
import logging
from collections import namedtuple
import time
import os
import shutil
import sys

_log_format = f"[%(asctime)s] %(message)s"

def get_logger(name : str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG)
    stream_handler.setFormatter(logging.Formatter(_log_format))
    logger.addHandler(stream_handler)
    return logger

logger = get_logger("YFPP")

class Peer:
    def __init__(self, address : str, url : str, world_part : str, country : str) -> None:
        self.__address = address
        self.__url = url
        self.__word_part = world_part
        self.__country = country
        self.__packet_loss = -1
        self.__rtt_min = 0.0
        self.__rtt_avg = 0.0
        self.__rtt_max = 0.0
        self.__rtt_mdev = 0.0
        self.__error = 0
        return

    def __lt__(self, other) -> bool:
        lesser = (self.__rtt_avg < other.__rtt_avg)
        return lesser

    def __str__(self) -> str:
        s = f"{self.__address} ({self.__word_part}/{self.__country})"
        if (self.__packet_loss >= 0):
            s += f" loss={self.__packet_loss} min={self.__rtt_min} avg={self.__rtt_avg} max={self.__rtt_max} mdev={self.__rtt_mdev}"
        return s

    def set_error(self, err : int) -> None:
        self.__error = err
        return

    def address(self) -> str:
        return self.__address

    def url(self) -> str:
        return self.__url

    def ping_success(self) -> bool:
        return (self.__error == 0)

    def parse_ping_output(self, ping_output : str) -> None:
        packet_loss_str = "packet loss"
        packet_loss_pos_end = ping_output.find(packet_loss_str)-2
        packet_loss_pos_start = ping_output.rfind(",", 0, packet_loss_pos_end)
        self.__packet_loss = int(ping_output[packet_loss_pos_start+2:packet_loss_pos_end])
        rtt_str = "rtt min/avg/max/mdev = "
        rtt_pos_start = ping_output.find(rtt_str) + len(rtt_str)
        rtt_min_pos_start = rtt_pos_start
        rtt_min_pos_end = ping_output.find("/", rtt_min_pos_start)
        self.__rtt_min = float(ping_output[rtt_min_pos_start:rtt_min_pos_end])
        rtt_avg_pos_start = rtt_min_pos_end + 1
        rtt_avg_pos_end = ping_output.find("/", rtt_avg_pos_start)
        self.__rtt_avg = float(ping_output[rtt_avg_pos_start:rtt_avg_pos_end])
        rtt_max_pos_start = rtt_avg_pos_end + 1
        rtt_max_pos_end = ping_output.find("/", rtt_max_pos_start)
        self.__rtt_max = float(ping_output[rtt_max_pos_start:rtt_max_pos_end])
        rtt_mdev_pos_start = rtt_max_pos_end + 1
        rtt_mdev_pos_end = ping_output.find(" ", rtt_mdev_pos_start)
        self.__rtt_mdev = float(ping_output[rtt_mdev_pos_start:rtt_mdev_pos_end])
        return

ProcessingPeer = namedtuple("ProcessingPeer", ["peer", "process"])

def parse_md(filename : str, word_part : str, country : str) -> list[Peer]:
    peers : list[Peer] = []
    with open(filename, 'r', encoding='UTF-8') as file:
        for line in file:
            s = line.rstrip()
            start_pos = s.find("* `tls://")
            if (start_pos == -1):
                start_pos = s.find("* `tcp://")
            if (start_pos == -1):
                continue
            url_start = start_pos + 3
            start_pos += 9
            url_end = s.rfind("`")
            end_pos = s.rfind(":")
            if ((end_pos == -1) or (url_end == -1)):
                continue
            if (s[start_pos] == "["):
                start_pos += 1
            if (s[end_pos-1] == "]"):
                end_pos -= 1
            address = s[start_pos:end_pos]
            url = s[url_start:url_end]
            peers.append(Peer(address, url, word_part, country))
    return peers

def get_peers() -> list[Peer]:
    peers : list[Peer] = []
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

def ping_peers(peers : list[Peer], parallel : int, pings : int, ping_interval : float) -> None:
    waiting_peers = peers.copy()
    processing : list[ProcessingPeer] = []
    while ((len(waiting_peers) != 0) or (len(processing) != 0)):
        if ((len(processing) < parallel) and (len(waiting_peers) != 0)):
            peer = waiting_peers.pop()
            logger.debug(f"ping start {peer}")
            commands = f'ping -c {pings} -q -i {ping_interval} "{peer.address()}" 2> /dev/null'
            process = subprocess.Popen(commands, shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            processing.append(ProcessingPeer(peer, process))
        time.sleep(0.1)
        processing_working : list[ProcessingPeer] = []
        for p in processing:
            process = p.process
            peer = p.peer
            poll = process.poll()
            if (poll is None):
                processing_working.append(p)
            elif (poll != 0):
                logger.debug(f"ping done {peer} with error")
                peer.set_error(poll)
            else:
                output_str = process.communicate()[0].decode("utf-8")
                peer.parse_ping_output(output_str)
                logger.debug(f"ping done {peer}")
        processing = processing_working
    return

def best_peers(peers : list[Peer], best : int) -> list[Peer]:
    best_peers = [peer for peer in peers if peer.ping_success()]
    logger.info(f"success ping {len(best_peers)} peers")
    best_peers.sort()
    best_peers = best_peers[0:best]
    return best_peers

def find_public_peers(parallel : int, pings : int, best : int, ping_interval : float) -> list[Peer]:
    logger.info(f"find public peers with parallel={parallel} pings_count={pings} best_count={best} ping_interval={ping_interval}")
    peers = get_peers()
    if (len(peers) != 0):
        ping_peers(peers, parallel, pings, ping_interval)
        peers = best_peers(peers, best)
    return peers

def save_to_yggdrasil_conf(yggdrasil_conf_filename : str, peers : list[Peer]) -> None:
    with open(yggdrasil_conf_filename, "r") as file:
        contents = file.readlines()

    new_contents : list[str] = []
    for line in contents:
        if (line.find("Peers: []") != -1):
            new_contents.append("  Peers: [\n")
            for peer in peers:
                new_contents.append(f"    {peer.url()}\n")
            new_contents.append("  ]\n")
        else:
            new_contents.append(line)

    with open(yggdrasil_conf_filename, "w") as file:
        new_content = "".join(new_contents)
        file.write(new_content)
    return

def main() -> None:
    import argparse
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
    parser.add_argument('--yggdrasil-conf', dest='yggdrasil_conf', metavar='YGGDRASIL_CONF', \
        type=str, default="", help='Save best peers to existing yggdrasil configuration file')
                    
    args = parser.parse_args()
    if (args.verbose):
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
    peers = find_public_peers(parallel=args.parallel, pings=args.pings, \
                              best=args.best, ping_interval=args.ping_interval)
    if (len(peers) == 0):
        logger.info(f"peers not found")
        sys.exit(1)
    else:
        logger.info(f"best peers:")
        for peer in peers:
            logger.info(f"  {peer}")
        if (args.yggdrasil_conf != 0):
            try:
                save_to_yggdrasil_conf(args.yggdrasil_conf, peers)
            except:
                sys.exit(1)
    sys.exit(0)

if __name__ == '__main__':
    main()