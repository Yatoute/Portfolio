import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

DATASETS_DIR = os.path.join(BASE_DIR, "dataset")

SFT_DATASET_PATH = os.path.join(DATASETS_DIR, "dataset_sft.jsonl")
SFT_TRAIN_PATH = os.path.join(DATASETS_DIR, "train.jsonl")
SFT_TEST_PATH = os.path.join(DATASETS_DIR, "test.jsonl")