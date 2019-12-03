# Copyright 2019 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import os
import logging
import time
import tensorflow as tf


logger = logging.getLogger(__name__)

"""
Helper function to determine in a checkpoint is present in the checkpoint_dir
"""
def has_checkpoint(checkpoint_dir):
    """
    :param checkpoint_dir: Directory to inspect for existing checkpoints
    :return: True if the directory has checkpoints, False if the directory is empty
    """
    if os.path.isdir(checkpoint_dir):           #Is the checkpoint_dir actually an existing directory
        if len(os.listdir(checkpoint_dir)) > 0: #Are there any files in the directory?
            return os.path.isfile(os.path.join(checkpoint_dir, "checkpoint")) #Return True of 'checkpoint' file exists

    return False


def wait_for_checkpoint(checkpoint_dir, data_store=None, retries=10):
    """
    block until there is a checkpoint in checkpoint_dir
    """
    for i in range(retries):
        if data_store:
            data_store.load_from_store()

        if has_checkpoint(checkpoint_dir):
            return
        time.sleep(10)

    raise ValueError((
        'Tried {retries} times, but checkpoint never found in '
        '{checkpoint_dir}'
    ).format(
        retries=retries,
        checkpoint_dir=checkpoint_dir,
    ))

def write_frozen_graph(graph_manager, local_path):
    if not os.path.exists(local_path):
        os.makedirs(local_path)
    # TODO: Supports only PPO
    output_head = ['main_level/agent/main/online/network_1/ppo_head_0/policy']
    frozen = tf.graph_util.convert_variables_to_constants(graph_manager.sess, graph_manager.sess.graph_def, output_head)
    tf.train.write_graph(frozen, local_path, 'model.pb', as_text=False)
    print("Saved TF frozen graph!")