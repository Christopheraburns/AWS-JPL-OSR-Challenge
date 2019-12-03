import argparse
import os
import logging
import sys
import imp

from rl_coach.core_types import EnvironmentEpisodes
from rl_coach.base_parameters import TaskParameters
from rl_coach.utils import short_dynamic_import

from markov.s3_boto_data_store import S3BotoDataStoreParameters, S3BotoDataStore
import markov.environments
from markov import utils

CUSTOM_FILES_PATH="robomaker"
PRESET_LOCAL_PATH = os.path.join(CUSTOM_FILES_PATH, "presets/")
ENVIRONMENT_LOCAL_PATH = os.path.join(CUSTOM_FILES_PATH, "environments/")

if not os.path.exists(CUSTOM_FILES_PATH):
    os.makedirs(CUSTOM_FILES_PATH)
    os.makedirs(PRESET_LOCAL_PATH)
    os.makedirs(ENVIRONMENT_LOCAL_PATH)

logger = logging.getLogger(__name__)

def evaluation_worker(graph_manager, number_of_trials, local_model_directory):
    # Initialize the graph
    task_parameters = TaskParameters()
    task_parameters.__dict__['checkpoint_restore_dir'] = local_model_directory
    graph_manager.create_graph(task_parameters)

    graph_manager.evaluate(EnvironmentEpisodes(number_of_trials))



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--markov-preset-file',
                        help="(string) Name of a preset file to run in Markov's preset directory.",
                        type=str,
                        default=os.environ.get("MARKOV_PRESET_FILE", "training_grounds.py"))
    parser.add_argument('--model-s3-bucket',
                        help='(string) S3 bucket where trained models are stored. It contains model checkpoints.',
                        type=str,
                        default=os.environ.get("MODEL_S3_BUCKET"))
    parser.add_argument('--model-s3-prefix',
                        help='(string) S3 prefix where trained models are stored. It contains model checkpoints.',
                        type=str,
                        default=os.environ.get("MODEL_S3_PREFIX"))
    parser.add_argument('--aws-region',
                        help='(string) AWS region',
                        type=str,
                        default=os.environ.get("ROS_AWS_REGION", "us-west-2"))
    parser.add_argument('--number-of-trials',
                        help='(integer) Number of trials',
                        type=int,
                        default=os.environ.get("NUMBER_OF_TRIALS", sys.maxsize))
    parser.add_argument('-c', '--local-model-directory',
                        help='(string) Path to a folder containing a checkpoint to restore the model from.',
                        type=str,
                        default='./checkpoint')

    args = parser.parse_args()
    data_store_params_instance = S3BotoDataStoreParameters(bucket_name=args.model_s3_bucket,
                                                           s3_folder=args.model_s3_prefix,
                                                           checkpoint_dir=args.local_model_directory,
                                                           aws_region=args.aws_region)
    data_store = S3BotoDataStore(data_store_params_instance)
    utils.wait_for_checkpoint(args.local_model_directory, data_store)

    preset_file_success = data_store.download_presets_if_present(PRESET_LOCAL_PATH)
    if preset_file_success:
        environment_file_success = data_store.download_environments_if_present(ENVIRONMENT_LOCAL_PATH)
        path_and_module = PRESET_LOCAL_PATH + args.markov_preset_file + ":graph_manager"
        graph_manager = short_dynamic_import(path_and_module, ignore_module_case=True)
        if environment_file_success:
            import robomaker.environments
        print("Using custom preset file!")
    elif args.markov_preset_file:
        markov_path = imp.find_module("markov")[1]
        preset_location = os.path.join(markov_path, "presets", args.markov_preset_file)
        path_and_module = preset_location + ":graph_manager"
        graph_manager = short_dynamic_import(path_and_module, ignore_module_case=True)
        print("Using custom preset file from Markov presets directory!")
    else:
        raise ValueError("Unable to determine preset file")

    graph_manager.data_store = data_store
    evaluation_worker(
        graph_manager=graph_manager,
        number_of_trials=args.number_of_trials,
        local_model_directory=args.local_model_directory
    )


if __name__ == '__main__':
    main()