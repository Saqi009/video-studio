#!/usr/bin/env bash
# Video Studio CLI. Usage: ./vs --help
cd "$(dirname "$0")" && exec python3 -m studio "$@"
