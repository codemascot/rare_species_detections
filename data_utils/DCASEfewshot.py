"""
Single use script to prepare developmentset for episodical training.

Based on adapted code from DL baseline system.

WARNING: script deletes earlier prepared data.

Issues & Questions:
- TODO: Add denoising support
- NOTE: Do we discard info by loading wavs as mono?


"""
import argparse
import os
from glob import glob
from itertools import chain
from tqdm import tqdm
import shutil
import torch
import pandas as pd
import numpy as np
import librosa
import soundfile as sf
import torchaudio.compliance.kaldi as ta_kaldi
import hashlib
import json
import csv
import matplotlib.pyplot as plt
from copy import copy
import noisereduce as nr

PLOT = False
PLOT_TOO_SHORT_SAMPLES = False
PLOT_SUPPORT = False


def normalize_mono(samples):
    current_max = max(np.amax(samples), -np.amin(samples))
    scale = 0.99 / current_max
    return np.multiply(samples, scale)


def denoise_signal(samples, sr):
    denoised_signal_samples = nr.reduce_noise(
        y=np.squeeze(samples),
        sr=sr,
        prop_decrease=0.95,
        stationary=True,
        time_mask_smooth_ms=25,
        freq_mask_smooth_hz=1000,
    )
    return denoised_signal_samples


def preprocess(
    # Adapted from BEATs
    self,
    source: torch.Tensor,
    fbank_mean: float = 15.41663,
    fbank_std: float = 6.55582,
    sample_frequency: int = 16000,
    frame_length: float = 25.0,
    frame_shift: float = 10.0,
    subtract_mean: bool = True,
) -> torch.Tensor:
    fbanks = []
    for waveform in source:
        waveform = waveform.unsqueeze(0) * 2**15
        fbank = ta_kaldi.fbank(
            waveform,
            num_mel_bins=128,
            sample_frequency=sample_frequency,
            frame_length=frame_length,
            frame_shift=frame_shift,
            subtract_mean=subtract_mean,
        )
        fbanks.append(fbank)
    fbank = torch.stack(fbanks, dim=0)
    fbank = (fbank - fbank_mean) / (2 * fbank_std)
    return fbank


def prepare_training_val_data(
    status,
    set_type,
    overwrite,
    tensor_length=128,
    frame_length=25.0,
    denoise=False,
    normalize=False,
    resample=False,
    target_fs=16000,
):
    """Prepare the Training_Set

    Training set is used for training *and* validating the encoder.

    All positive samples are converted into mel features from which
    a random tensor_length long window can be selected. If PLOT=True,
    pngs are saved to separate folder showing the selected features.

    All input feature tensors and their labels are saved into a single
    pickle. Separate directories are created for different params, so
    that they can be found again during training.
    """

    def preprocess_df(df):
        # for each tagged sample
        for ind, _ in df.iterrows():
            temp_plot = False
            # obtain class label for current sample
            label = cls_list[ind]
            # ensure there were enough positives of the class
            if label not in min_segment_lengths:
                continue
            # obtain a segment with large margins around event
            extra_time = 3
            frame_shift = np.round(min_segment_lengths[label] / tensor_length * 1000)
            frame_shift = 1 if frame_shift < 1 else frame_shift
            start_waveform = int((df["Starttime"][ind] - extra_time) * fs)

            end_waveform = int((df["Endtime"][ind] + extra_time) * fs)
            end_waveform = len(y) if len(y) < end_waveform else end_waveform
            if start_waveform < 0:
                extra_time = (extra_time * fs + start_waveform) / fs
                start_waveform = 0
            current_segment = y[start_waveform:end_waveform]
            if resample:
                current_segment = librosa.resample(
                    current_segment, orig_sr=fs, target_sr=target_fs
                )
                # only resampling segment, so overall fs doesn't change

            # normalize
            if normalize:
                current_segment = normalize_mono(current_segment)

            # denoise
            if denoise:
                current_segment = denoise_signal(current_segment, target_fs)

            # obtain mel bins
            fbank = preprocess(
                None,
                torch.Tensor(current_segment[None, :]),
                sample_frequency=target_fs,
                frame_length=frame_length,
                frame_shift=frame_shift,
            )
            data = fbank.data[0].T
            # select the relevant segment (without the large margins)
            if status == "validate" or status == "test":
                extra_margin = 0
            else:
                extra_margin = min_segment_lengths[label] / 3
            x_start = int(np.round((extra_time - extra_margin) / frame_shift * 1000))
            x_start = 0 if x_start < 0 else x_start
            x_end = int(
                np.round(
                    (
                        df["Endtime"][ind]
                        - df["Starttime"][ind]
                        + extra_time
                        + extra_margin
                    )
                    / frame_shift
                    * 1000
                )
            )
            x_end = data.shape[1] if x_end > data.shape[1] else x_end
            input_feature = data[:, x_start:x_end]
            # ensure minimal length equals tensor length
            if x_end - x_start < tensor_length:
                print(
                    "_".join(
                        [
                            glob_cls_name,
                            os.path.splitext(file_name)[0],
                            label,
                            str(df["Starttime"][ind]),
                        ],
                    )
                    + " had to be repeated. FS="
                    + str(fs)
                )
                temp_plot = PLOT_TOO_SHORT_SAMPLES
                # x_end = x_start + tensor_length
                # if x_end > data.shape[1]:
                #     x_end = data.shape[1]
                #     x_start = x_end - tensor_length
                flip_flop_i = 0
                while input_feature.shape[1] < tensor_length:
                    input_feature = torch.cat(
                        (
                            input_feature,
                            torch.flip(input_feature, [1]),
                        ),
                        1,
                    )
                    flip_flop_i += 1
                # input_feature = input_feature.repeat(
                #     (1, tensor_length // input_feature.shape[1] + 1)
                # )
            assert input_feature.shape[1] >= tensor_length
            # store feature
            input_features.append(input_feature.numpy())
            labels.append(label)
            # plot feature
            if PLOT or temp_plot or (status != "train" and PLOT_SUPPORT):
                plt.imshow(input_feature, cmap="hot", interpolation="nearest")
                plt.title(label)
                plt.savefig(
                    os.path.join(
                        target_path,
                        "plots",
                        "_".join(
                            [
                                glob_cls_name,
                                os.path.splitext(file_name)[0],
                                label,
                                str(df["Starttime"][ind]),
                            ],
                        )
                        + ".png",
                    )
                )
            if status == "validate" and len(labels) == len(df):
                np.savez(
                    os.path.join(
                        target_path,
                        "audio",
                        "support_data_" + os.path.splitext(file_name)[0],
                    ),
                    *input_features
                )
                np.save(
                    os.path.join(
                        target_path,
                        "audio",
                        "support_labels_" + os.path.splitext(file_name)[0],
                    ),
                    np.asarray(labels),
                )
                break

    # Root directory of data to be processed
    root_dir = "/data/DCASE/Development_Set"

    # Create directories for saving
    my_hash_dict = {
        "resample": resample,
        "denoise": denoise,
        "normalize": normalize,
        "frame_length": frame_length,
        "tensor_length": tensor_length,
        "set_type": set_type,
    }
    if resample:
        my_hash_dict["tartget_fs"] = target_fs
    hash_dir_name = hashlib.sha1(
        json.dumps(my_hash_dict, sort_keys=True).encode()
    ).hexdigest()
    target_path = os.path.join("/data/DCASEfewshot", status, hash_dir_name)
    if overwrite:
        if os.path.exists(target_path):
            shutil.rmtree(target_path)

    if not os.path.exists(os.path.join(target_path, "audio")):
        os.makedirs(os.path.join(target_path, "audio"))

    if not os.path.exists(os.path.join(target_path, "plots")):
        os.makedirs(os.path.join(target_path, "plots"))

    print("=== Processing data ===")
    # collect all meta files, one for each audio file
    all_csv_files = [
        file
        for path_dir, _, _ in os.walk(os.path.join(root_dir, set_type))
        for file in glob(os.path.join(path_dir, "*.csv"))
    ]

    # loop through all meta files
    labels = []
    input_features = []  # list of tuples (input tensor,label)
    save_temp_ind = 0
    for file in tqdm(all_csv_files):
        if status == "validate":
            input_features = []
            labels = []
        # read csv file into df
        split_list = file.split("/")
        glob_cls_name = split_list[split_list.index(set_type) + 1]
        file_name = split_list[split_list.index(set_type) + 2]
        df = pd.read_csv(file, header=0, index_col=False)

        # read audio file into y
        audio_path = file.replace("csv", "wav")
        print("Processing file name {}".format(audio_path))
        y, fs = librosa.load(audio_path, sr=None, mono=True)
        if not resample:
            target_fs = fs
        df = df[(df == "POS").any(axis=1)]
        df = df.reset_index()

        # For csv files with a column name Call, pick up the global class name
        if "CALL" in df.columns:
            cls_list = [glob_cls_name] * len(df)
        elif "Q" in df.columns:
            cls_list = ["POS"] * len(df)
        else:
            cls_list = [
                df.columns[(df == "POS").loc[index]].values
                for index, row in df.iterrows()
            ]
            cls_list = list(chain.from_iterable(cls_list))

        # get average/min segment length for first five positives
        average_segment_lengths = {}
        min_segment_lengths = {}
        for class_column in df:
            # get label
            if class_column in ["Audiofilename", "Starttime", "Endtime", "index"]:
                continue
            if class_column == "CALL":
                label = glob_cls_name
            elif class_column == "Q":
                label = "POS"
            else:
                label = class_column
            # get first five positives
            first_5_pos_ind = df.index[df[class_column] == "POS"].tolist()[0:5]
            if len(first_5_pos_ind) < 5:
                if status == "train":
                    continue
                else:
                    assert 1 == 0
            average_segment_lengths[label] = np.average(
                df["Endtime"][first_5_pos_ind] - df["Starttime"][first_5_pos_ind]
            )
            min_segment_lengths[label] = np.min(
                df["Endtime"][first_5_pos_ind] - df["Starttime"][first_5_pos_ind]
            )
        if status == "validate" or status == "test":
            if resample:
                y = librosa.resample(y, orig_sr=fs, target_sr=target_fs)
                fs = target_fs

            if normalize:
                y = normalize_mono(y)

            if denoise:
                y = denoise_signal(y, target_fs)

            # CREATE QUERY SETS
            # obtain file specific frame_shift and save to meta.csv
            frame_shift = np.round(min_segment_lengths["POS"] / tensor_length * 1000)
            frame_shift = 1 if frame_shift < 1 else frame_shift
            with open(
                os.path.join(target_path, "audio", "meta.csv"),
                "a",
                newline="",
                encoding="utf-8",
            ) as my_file:
                wr = csv.writer(my_file, delimiter=",")
                wr.writerow([frame_shift, os.path.splitext(file_name)[0]])
            # get mel for entire file
            fbank = preprocess(
                None,
                torch.Tensor(y[None, :]),
                sample_frequency=target_fs,
                frame_length=frame_length,
                frame_shift=frame_shift,
            )
            data = fbank.data[0].T
            # obtain windows and their labels
            segment_overlap = 0.5
            segment_hop = int(round(tensor_length * segment_overlap))
            segment_ind = 0
            input_features = []
            labels = []
            interval_array = pd.arrays.IntervalArray.from_arrays(
                df["Starttime"].values, df["Endtime"].values
            )
            segment_end_ind = 0
            while segment_end_ind < data.shape[1]:
                # add feature
                segment_start_ind = segment_ind * segment_hop
                segment_end_ind = segment_start_ind + tensor_length
                input_feature = data[:, segment_start_ind:segment_end_ind]
                # pad too short features (at end of file) with zeros
                if not input_feature.shape[1] == tensor_length:
                    input_feature = torch.cat(
                        (
                            input_feature,
                            torch.zeros(
                                (
                                    input_feature.shape[0],
                                    tensor_length - input_feature.shape[1],
                                )
                            ),
                        ),
                        1,
                    )
                input_features.append(input_feature.numpy())
                # check if included in df
                segment_interval = pd.Interval(
                    segment_start_ind / 1000, segment_end_ind / 1000
                )
                is_included = np.any(interval_array.overlaps(segment_interval))
                # add label
                label = "POS" if is_included else "NEG"
                labels.append(label)
                segment_ind += 1
                if PLOT:
                    plt.imshow(input_feature, cmap="hot", interpolation="nearest")
                    plt.title(label)
                    plt.savefig(
                        os.path.join(
                            target_path,
                            "plots",
                            "_".join(
                                [
                                    "query",
                                    glob_cls_name,
                                    os.path.splitext(file_name)[0],
                                    label,
                                    str(segment_start_ind),
                                ],
                            )
                            + ".png",
                        )
                    )

            np.savez(
                os.path.join(
                    target_path, "audio", "query_data_" + os.path.splitext(file_name)[0]
                ),
                *input_features
            )
            np.save(
                os.path.join(
                    target_path,
                    "audio",
                    "query_labels_" + os.path.splitext(file_name)[0],
                ),
                np.asarray(labels),
            )
            input_features = []
            labels = []
            # CREATE SUPPORT SETS
            # reduce df to 5 lines and class list
            df = df.head(5)
            neg_starttimes = []
            neg_endtimes = []
            last_pos_end_time = 0.0
            q_labels = []
            first_pos_starts_at_zero = False
            for sample_ind, row in df.iterrows():
                if row["Starttime"] == 0:
                    first_pos_starts_at_zero = True
                # get endtime for negative sample
                new_neg_endtime = row["Starttime"] - 0.1
                # ensure neg sample has starts before it ends
                if new_neg_endtime <= last_pos_end_time:
                    # if not select 2ms centered segment to be repeated later
                    end_neg = row["Starttime"]
                    if sample_ind == 0:
                        start_neg = 0
                    else:
                        start_neg = df["Endtime"][sample_ind - 1]
                    last_pos_end_time = start_neg + (end_neg - start_neg) / 2 - 0.01
                    new_neg_endtime = last_pos_end_time + 0.02
                # append neg segment
                neg_starttimes.append(last_pos_end_time)
                neg_endtimes.append(new_neg_endtime)
                q_labels.append("NEG")
                # select new starttime for next negative
                last_pos_end_time = row["Endtime"] + 0.1
                # ensure neg starttime doesn't start after onset new pos
                if sample_ind < 4:
                    last_pos_end_time = (
                        row["Endtime"]
                        if last_pos_end_time > df["Starttime"][sample_ind + 1]
                        else last_pos_end_time
                    )
                # append pos segment
                duration = row["Endtime"] - row["Starttime"]
                margin = min_segment_lengths["POS"] / 3
                if duration + 2 * margin < tensor_length * frame_shift / 1000:
                    margin = tensor_length * frame_shift / 1000 - duration

                pos_start_time = (
                    0 if row["Starttime"] - margin < 0 else row["Starttime"] - margin
                )
                neg_starttimes.append(pos_start_time)
                neg_endtimes.append(row["Endtime"] + margin)
                q_labels.append("POS")

            df = pd.DataFrame(
                {
                    "Audiofilename": [file_name] * len(neg_starttimes),
                    "Starttime": neg_starttimes,
                    "Endtime": neg_endtimes,
                    "Q": q_labels,
                }
            )
            if first_pos_starts_at_zero:
                max_neg_idx = (
                    df[df["Q"] == "NEG"]["Endtime"] - df[df["Q"] == "NEG"]["Starttime"]
                ).idxmax()
                df["Starttime"][0] = df["Starttime"][max_neg_idx]
                df["Endtime"][0] = df["Endtime"][max_neg_idx]
            cls_list = df["Q"].values
            min_segment_lengths["NEG"] = min_segment_lengths["POS"]
            assert np.all(df["Endtime"] - df["Starttime"] > 0)

        preprocess_df(df)
    # save preprocessed data
    if status == "train":
        np.savez(os.path.join(target_path, "audio", "data"), *input_features)
        np.save(os.path.join(target_path, "audio", "labels"), np.asarray(labels))

    print(" Feature extraction complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--status",
        help=" 'train' or 'validate' ",
        default="train",
        required=False,
        type=str,
    )

    parser.add_argument(
        "--set_type",
        help=" 'Training_Set' or 'Validation_Set' or 'Evaluation_Set",
        default="Training_Set",
        required=False,
        type=str,
    )

    parser.add_argument(
        "--overwrite",
        help="If there's an existing folder, should it be deleted?",
        default=False,
        required=False,
        action="store_true",
    )
    parser.add_argument(
        "--normalize",
        help="Normalize the waveform during preprocessing?",
        default=False,
        required=False,
        action="store_true",
    )

    parser.add_argument(
        "--resample",
        help="Resample the waveform during preprocessing?",
        default=False,
        required=False,
        action="store_true",
    )
    parser.add_argument(
        "--target_fs",
        help="Sampling frequency to resample to if --resample is True",
        default=16000,
        required=False,
        type=int,
    )

    parser.add_argument(
        "--frame_length",
        help="Frame length in ms for the mel features",
        default=25.0,
        required=False,
        type=float,
    )
    parser.add_argument(
        "--denoise",
        help="Should waveform be denoised during preprocessing?",
        default=False,
        required=False,
        action="store_true",
    )
    parser.add_argument(
        "--tensor_length",
        help="Length of the final feature tensor",
        default=128,
        required=False,
        type=int,
    )
    # check input
    cli_args = parser.parse_args()
    assert (
        cli_args.status == "validate"
        or cli_args.status == "train"
        or cli_args.status == "test"
    )
    if cli_args.status == "validate":
        assert cli_args.set_type == "Validation_Set"
    elif cli_args.status == "train":
        assert cli_args.set_type == "Training_Set"
    elif cli_args.status == "test":
        assert cli_args.set_type == "Evaluation_Set"

    prepare_training_val_data(
        cli_args.status,
        cli_args.set_type,
        cli_args.overwrite,
        cli_args.tensor_length,
        cli_args.frame_length,
        cli_args.denoise,
        cli_args.normalize,
        cli_args.resample,
        cli_args.target_fs,
    )
