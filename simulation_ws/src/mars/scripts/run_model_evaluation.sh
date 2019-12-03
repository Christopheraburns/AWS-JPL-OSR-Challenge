#!/usr/bin/env bash

set -ex

export PYTHONUNBUFFERED=1

export NODE_TYPE=SIMULATION_WORKER

python3 -m markov.mars_evaluation
