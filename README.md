# Yggdrasil Public Peers Finder (YFPP)

## Description

A Python utility to automatically discover, test, and select the best [Yggdrasil](https://yggdrasil-network.github.io/) 
network [public peers](https://github.com/yggdrasil-network/public-peers) based on performance metrics, with features 
for geographic distribution, caching, and automated configuration updates.


## Features

- **Automatic Peer Discovery**: Fetches current public peers from Yggdrasil's GitHub repository
- **Performance Testing**: Tests peers using parallel ping operations to measure latency and packet loss
- **Smart Selection**: Chooses best peers based on latency with optional country distribution limits
- **Config Management**: Automatically updates Yggdrasil configuration files
- **Caching**: Optional JSON caching of peer lists to reduce GitHub API calls
- **Flexible Configuration**: Extensive command-line options for customization

## Installation

### Prerequisites

- Python 3.7+
- Required packages: `tqdm`, `hjson`

```bash
pip install tqdm hjson
```

## System Requirements

 - Linux system with ping command available
 - Git (for fetching peers from repository)
 - Write access to Yggdrasil configuration file

## Usage

### Basic Usage
```bash
python yfpp.py --yggdrasil-conf /etc/yggdrasil.conf
```

This will:
 1. Fetch public peers from the default repository
 2. Test all peers with ping
 3. Select the 5 best peers (default)
 4. Update your Yggdrasil configuration

### Advanced Examples
```bash
# Test more thoroughly and select 10 best peers
python yfpp.py --pings 10 --best 10 --yggdrasil-conf /etc/yggdrasil.conf

# Limit to 2 peers per country for geographic diversity
python yfpp.py --best 10 --max-from-country 2 --yggdrasil-conf /etc/yggdrasil.conf

# Use custom repository and cache peers locally
python yfpp.py --repo-url https://github.com/custom/peers \
              --yggdrasil-peers-json peers.json \
              --yggdrasil-conf /etc/yggdrasil.conf

# Force update even if config has existing peers
python yfpp.py --force --yggdrasil-conf /etc/yggdrasil.conf
```

## Command Line Options

### Core Options

 - --yggdrasil-conf FILE - Yggdrasil configuration file to update (required)
 - --best N - Number of best peers to select (default: 5)
 - --parallel N - Number of parallel ping processes (default: 10)
 - --pings N - Ping packets per peer (default: 5)
 - --ping-interval SEC - Seconds between pings (default: 0.1)

### Geographic Distribution

 - --max-from-country N - Maximum peers from any single country (0 = unlimited)

### Behavior Control

 - --force - Run even if config has existing peers
 - --rewrite-config-peers - Overwrite existing peers in config
 - --quiet - Minimal logging output
 - --verbose - Detailed debugging output

### Advanced

 - --yggdrasil-peers-json FILE - JSON file to cache peers data
 - --repo-url URL - Custom peers repository URL

## How It Works

 1. Peer Discovery: Fetches peer lists from regional markdown files in the Yggdrasil public peers repository
 2. Performance Testing: Uses parallel ping commands to test latency and packet loss for each peer
 3. Selection Algorithm: Sorts peers by average latency and applies country distribution limits if specified
 4. Config Update: Replaces the Peers section in your Yggdrasil configuration with the selected peers

## Caching

The tool can cache fetched peers in a JSON file using the --yggdrasil-peers-json option. This is useful for:

 - Reducing load on GitHub API
 - Working offline
 - Faster subsequent runs

## Error Handling

 - Exits gracefully if no suitable peers are found
 - Validates file permissions before making changes
 - Provides clear error messages for common issues
 - Falls back to cached data if network is unavailable

## Troubleshooting

### Common Issues

 - Permission Denied: Ensure write access to the Yggdrasil config file
 - No Peers Found: Check internet connectivity and GitHub access
 - High Ping Times: Adjust --pings and --ping-interval for better accuracy

### Debug Mode

Use --verbose for detailed logging:
```bash
python yfpp.py --verbose --yggdrasil-conf /etc/yggdrasil.conf
```

## License

MIT License - see [LICENSE](LICENSE) file for details.