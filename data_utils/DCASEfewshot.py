"""
Single use script to prepare developmentset for episodical training.

Based on adapted code from DL baseline system.

WARNING: script deletes earlier prepared data.

Issues & Questions:
- So far, only training set is processed
- Do we discard info by loading wavs as mono?
- As in baseline, the script now adds 0.025 ms margins at start and tail, why?
- All wav resampled to 16 kHz - probably not ideal
"""
import os
from glob import glob
from itertools import chain
from tqdm import tqdm
import shutil

import pandas as pd
import numpy as np
import librosa
import soundfile as sf


def time_2_sample(df, sr):
    """Convert starttime and endtime to sample index.

    NOTE: Also adds margin of 25 ms around the onset and offsets.
    """
    # add margins
    df.loc[:, "Starttime"] = df["Starttime"] - 0.025
    df.loc[:, "Endtime"] = df["Endtime"] + 0.025

    # get indices
    start_sample = [int(np.floor(start * sr)) for start in df["Starttime"]]
    end_sample = [int(np.floor(end * sr)) for end in df["Endtime"]]

    return start_sample, end_sample


def prepare_training_data():
    """ Prepare the Training_Set
    
    Training set is used for training and validating the encoder.

    All positive samples are saved as separate wav files, 
    and the relevant meta data is saved in a csv file. 
    """
    # Create directories for saving output
    root_dir = "/data/DCASE/Development_Set"
    target_path = "/data/DCASEfewshot"
    if os.path.exists(target_path):
        shutil.rmtree(target_path)
    os.makedirs(os.path.join(target_path, "audio", "train"))
    os.makedirs(os.path.join(target_path, "meta"))

    print("=== Processing training set ===")

    # collect all meta files, one for each audio file
    all_csv_files = [
        file
        for path_dir, _, _ in os.walk(os.path.join(root_dir, "Training_Set"))
        for file in glob(os.path.join(path_dir, "*.csv"))
    ]

    # loop through all meta files
    df_train_list = [] # list from which meta df will be created
    for file in tqdm(all_csv_files):
        # read csv file into df
        split_list = file.split("/")
        glob_cls_name = split_list[split_list.index("Training_Set") + 1]
        file_name = split_list[split_list.index("Training_Set") + 2]
        df = pd.read_csv(file, header=0, index_col=False)
        
        # read audio file into y
        audio_path = file.replace("csv", "wav")
        print("Processing file name {}".format(audio_path))
        y, fs = librosa.load(audio_path, sr=16000, mono=True)
        df_pos = df[(df == "POS").any(axis=1)]

        # Obtain indices for start and end of positive intervals
        start_sample, end_sample = time_2_sample(df_pos, sr=16000)
        start_time = df["Starttime"]

        # For csv files with a column name Call, pick up the global class name
        if "CALL" in df_pos.columns:
            cls_list = [glob_cls_name] * len(start_sample)
        else:
            cls_list = [
                df_pos.columns[(df_pos == "POS").loc[index]].values
                for index, row in df_pos.iterrows()
            ]
            cls_list = list(chain.from_iterable(cls_list))

        # Ensure all samples have both a start and end time
        assert len(start_sample) == len(end_sample)
        assert len(cls_list) == len(start_sample)

        for index, _ in enumerate(start_sample):
            # obtain class label for current sample
            label = cls_list[index]
            
            # obtain path for wav file for current sample
            file_path_out = os.path.join(
                target_path,
                "audio",
                "train",
                "_".join(
                    [
                        glob_cls_name,
                        os.path.splitext(file_name)[0],
                        label,
                        str(start_time[index]),
                    ]
                )
                + ".wav",
            )

            # collect meta data of sample
            df_train_list.append(
                [
                    os.path.splitext(file_name)[0],
                    glob_cls_name,
                    label,
                    start_sample[index],
                    end_sample[index],
                    audio_path,
                    file_path_out,
                ]
            )

            # write wav file
            samples = y[start_sample[index] : end_sample[index]]
            sf.write(file_path_out, samples, 16000)

    # save meta data to csv file
    train_df = pd.DataFrame(df_train_list)
    train_df.to_csv(
        os.path.join(
            target_path,
            "meta",
            "train.csv",
        ),
        header=[
            "filename",
            "dataset_name",
            "category",
            "start_sample",
            "end_sample",
            "src_file",
            "filepath",
        ],
    )

    print(" Feature extraction for training set complete")


if __name__ == "__main__":
    prepare_training_data()
