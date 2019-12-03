import io
import os
import time
import json
import boto3
from google.protobuf import text_format
from tensorflow.python.training.checkpoint_state_pb2 import CheckpointState
from rl_coach.data_stores.data_store import DataStore, DataStoreParameters
from markov import utils

CHECKPOINT_METADATA_FILENAME="checkpoint"
SLEEP_TIME_WHILE_WAITING_FOR_DATA_FROM_TRAINER_IN_SECOND = 2
IP_KEY = "IP"


class S3BotoDataStoreParameters(DataStoreParameters):
    def __init__(self, aws_region: str = "us-east-1", bucket_name: str = None, s3_folder: str = None,
                 checkpoint_dir: str = None):
        super().__init__("s3", "", "")
        self.aws_region = aws_region
        self.bucket = bucket_name
        self.s3_folder = s3_folder
        self.checkpoint_dir = checkpoint_dir
        self.lock_file = ".lock"


class S3BotoDataStore(DataStore):
    def __init__(self, params: S3BotoDataStoreParameters):
        self.params = params
        self.key_prefix = os.path.join(self.params.s3_folder, "model/")
        self.ip_data_key = os.path.join(self.params.s3_folder, "ip/ip.json")
        self.ip_done_key = os.path.join(self.params.s3_folder, "ip/done")
        self.preset_data_prefix = os.path.join(self.params.s3_folder, "presets/")
        self.environment_data_prefix = os.path.join(self.params.s3_folder, "environments/")

    def deploy(self) -> bool:
        return True

    def get_info(self):
        return "s3://{}/{}".format(self.params.bucket, self.params.s3_folder)

    def undeploy(self) -> bool:
        return True

    def save_to_store(self):
        try:
            s3_client = self._get_client()

            if self.graph_manager:
                utils.write_frozen_graph(self.graph_manager, self.params.checkpoint_dir)

            # Delete any existing lock file
            s3_client.delete_object(Bucket=self.params.bucket, Key=self._get_s3_key(self.params.lock_file))

            # We take a lock by writing a lock file to the same location in S3
            s3_client.upload_fileobj(Fileobj=io.BytesIO(b''),
                                     Bucket=self.params.bucket,
                                     Key=self._get_s3_key(self.params.lock_file))

            # Start writing the model checkpoints to S3
            checkpoint_file = None
            for root, dirs, files in os.walk(self.params.checkpoint_dir):
                for filename in files:
                    # Skip the checkpoint file that has the latest checkpoint number
                    if filename == CHECKPOINT_METADATA_FILENAME:
                        checkpoint_file = (root, filename)
                        continue

                    # Upload all the other files from the checkpoint directory
                    abs_name = os.path.abspath(os.path.join(root, filename))
                    rel_name = os.path.relpath(abs_name, self.params.checkpoint_dir)
                    s3_client.upload_file(Filename=abs_name,
                                          Bucket=self.params.bucket,
                                          Key=self._get_s3_key(rel_name))

            # After all the checkpoint files have been uploaded, we upload the version file.
            abs_name = os.path.abspath(os.path.join(checkpoint_file[0], checkpoint_file[1]))
            rel_name = os.path.relpath(abs_name, self.params.checkpoint_dir)
            s3_client.upload_file(Filename=abs_name,
                                  Bucket=self.params.bucket,
                                  Key=self._get_s3_key(rel_name))

            # Release the lock by deleting the lock file from S3
            s3_client.delete_object(Bucket=self.params.bucket, Key=self._get_s3_key(self.params.lock_file))

            checkpoint = self._get_current_checkpoint()
            if checkpoint:
                checkpoint_number = self._get_checkpoint_number(checkpoint)
                checkpoint_number_to_delete = checkpoint_number - 4

                # List all the old checkpoint files that needs to be deleted
                response = s3_client.list_objects_v2(Bucket=self.params.bucket,
                                                     Prefix=self._get_s3_key(str(checkpoint_number_to_delete) + "_"))
                if "Contents" in response:
                    num_files = 0
                    for obj in response["Contents"]:
                        s3_client.delete_object(Bucket=self.params.bucket,
                                                Key=obj["Key"])
                        num_files += 1

                    print("Deleted %s model files from S3" % num_files)
                    return True
        except Exception as e:
            raise e

    def load_from_store(self, expected_checkpoint_number=-1):
        try:
            filename = os.path.abspath(os.path.join(self.params.checkpoint_dir, CHECKPOINT_METADATA_FILENAME))
            if not os.path.exists(self.params.checkpoint_dir):
                os.makedirs(self.params.checkpoint_dir)

            while True:
                s3_client = self._get_client()
                response = s3_client.list_objects_v2(Bucket=self.params.bucket,
                                                     Prefix=self._get_s3_key(self.params.lock_file))

                if "Contents" not in response:
                    try:
                        # If no lock is found, try getting the checkpoint
                        s3_client.download_file(Bucket=self.params.bucket,
                                                Key=self._get_s3_key(CHECKPOINT_METADATA_FILENAME),
                                                Filename=filename)
                    except Exception as e:
                        print("Got exception while downloading checkpoint", e)
                        time.sleep(SLEEP_TIME_WHILE_WAITING_FOR_DATA_FROM_TRAINER_IN_SECOND)
                        continue
                else:
                    time.sleep(SLEEP_TIME_WHILE_WAITING_FOR_DATA_FROM_TRAINER_IN_SECOND)
                    continue

                checkpoint = self._get_current_checkpoint()
                if checkpoint:
                    checkpoint_number = self._get_checkpoint_number(checkpoint)

                    # if we get a checkpoint that is older that the expected checkpoint, we wait for
                    #  the new checkpoint to arrive.
                    if checkpoint_number < expected_checkpoint_number:
                        time.sleep(SLEEP_TIME_WHILE_WAITING_FOR_DATA_FROM_TRAINER_IN_SECOND)
                        continue

                    # Found a checkpoint to be downloaded
                    response = s3_client.list_objects_v2(Bucket=self.params.bucket,
                                                         Prefix=self._get_s3_key(checkpoint.model_checkpoint_path))
                    if "Contents" in response:
                        num_files = 0
                        for obj in response["Contents"]:
                            # Get the local filename of the checkpoint file
                            filename = os.path.abspath(os.path.join(self.params.checkpoint_dir,
                                                                    obj["Key"].replace(self.key_prefix, "")))
                            s3_client.download_file(Bucket=self.params.bucket,
                                                    Key=obj["Key"],
                                                    Filename=filename)
                            num_files += 1
                        print("Downloaded %s model files from S3" % num_files)
                        return True

        except Exception as e:
            print("Got exception while loading model from S3", e)
            raise e

    def store_ip(self, ip_address):
        s3_client = self._get_client()
        ip_data = {IP_KEY: ip_address}
        ip_data_json_blob = json.dumps(ip_data)
        ip_data_file_object = io.BytesIO(ip_data_json_blob.encode())
        ip_done_file_object = io.BytesIO(b'done')
        s3_client.upload_fileobj(ip_data_file_object, self.params.bucket, self.ip_data_key)
        s3_client.upload_fileobj(ip_done_file_object, self.params.bucket, self.ip_done_key)

    def get_ip(self):
        self._wait_for_ip_upload()
        s3_client = self._get_client()
        try:
            s3_client.download_file(self.params.bucket, self.ip_data_key, 'ip.json')
            with open("ip.json") as f:
                ip_address = json.load(f)[IP_KEY]
            return ip_address
        except Exception as e:
            raise RuntimeError("Cannot fetch IP of redis server running in SageMaker:", e)

    def download_presets_if_present(self, local_path):
        return self._download_directory(self.params.bucket, self.preset_data_prefix, local_path)

    def download_environments_if_present(self, local_path):
        return self._download_directory(self.params.bucket, self.environment_data_prefix, local_path)

    def get_current_checkpoint_number(self):
        return self._get_checkpoint_number(self._get_current_checkpoint())

    def _download_directory(self, s3_bucket, s3_prefix, local_path):
        s3_client = self._get_client()
        response = s3_client.list_objects_v2(Bucket=s3_bucket,
                                             Prefix=s3_prefix)

        if "Contents" not in response:
            return False

        if "Contents" in response:
            try:
                for obj in response["Contents"]:
                    filename = os.path.abspath(os.path.join(local_path,
                                                            obj["Key"].replace(s3_prefix, "")))
                    if s3_client.download_file(Bucket=s3_bucket, Key=obj["Key"], Filename=filename) == False:
                        print("Failed downloading object, Bucket: ", s3_bucket,
                              ", Key ", obj["key"])
                        return False
            except Exception as e:
                print("Got exception while downloading checkpoint", e)
                return False

        return True

    def _get_s3_key(self, key):
        return os.path.normpath(os.path.join(self.key_prefix, key))

    def _get_client(self):
        session = boto3.session.Session()
        return session.client('s3', region_name=self.params.aws_region)

    def _wait_for_ip_upload(self, timeout_in_second=600):
        start_time = time.time()
        s3_client = self._get_client()
        while True:
            response = s3_client.list_objects(Bucket=self.params.bucket, Prefix=self.ip_done_key)
            if "Contents" not in response:
                time.sleep(SLEEP_TIME_WHILE_WAITING_FOR_DATA_FROM_TRAINER_IN_SECOND)

                time_elapsed_in_second = time.time() - start_time
                if time_elapsed_in_second % 5 == 0:
                    print("Waiting for SageMaker Redis server IP... Time elapsed: %s seconds" % time_elapsed_in_second)
                if time_elapsed_in_second % 300 == 0:
                    # Recreate the client with new credential every 5 minutes
                    s3_client = self._get_client()
                if time_elapsed_in_second >= timeout_in_second:
                    raise RuntimeError("Cannot retrieve IP of redis server running in SageMaker")
            else:
                break

    def _get_current_checkpoint(self):
        try:
            checkpoint_metadata_filepath = os.path.abspath(
                os.path.join(self.params.checkpoint_dir, CHECKPOINT_METADATA_FILENAME))
            checkpoint = CheckpointState()
            if os.path.exists(checkpoint_metadata_filepath) == False:
                return None

            contents = open(checkpoint_metadata_filepath, 'r').read()
            text_format.Merge(contents, checkpoint)
            return checkpoint
        except Exception as e:
            print("Got exception while reading checkpoint metadata", e)
            raise e

    def _get_checkpoint_number(self, checkpoint):
        checkpoint_relative_path = checkpoint.model_checkpoint_path
        return int(checkpoint_relative_path.split('_Step')[0])
