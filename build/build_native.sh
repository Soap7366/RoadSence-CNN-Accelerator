#!/bin/sh
set -eu
cd "$(dirname "$0")/../src/stream"
sh native/build.sh
