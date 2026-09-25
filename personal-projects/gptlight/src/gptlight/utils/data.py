from __future__ import annotations

from typing import Optional, Any

import requests
import json
import urllib.request
import zipfile
import io
import logging
import pandas as pd


def fetch_verdict_text(to_file_path: Optional[str] = None) -> str:
    """
    Fetch Verdict Data Set
    """
    
    file_id = "1PhOg8KHJfT6J6ZOfdGIOsS7JpLcMiARf"
    url = f"https://drive.google.com/uc?export=download&id={file_id}"

    resp = requests.get(url)
    resp.raise_for_status()

    text_data = resp.text

    if to_file_path:
        with open(to_file_path, "w", encoding="utf-8") as f:
            f.write(text_data)

    return text_data


def fetch_sms_spam_collection() -> Optional[pd.DataFrame] :
    """ 
    Fetch sms and spam collection
    """
    
    url = "https://archive.ics.uci.edu/static/public/228/sms+spam+collection.zip"
    
    try:
        response = urllib.request.urlopen(url)
        
        zip_data = response.read()
        
        with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
            with z.open("SMSSpamCollection") as f:
                df = pd.read_csv(f, sep="\t", header=None, names=["Label", "Text"])
    except Exception as e:
        logging.error(f"Error when fetching the sms and spam collection : {e}")
        return None

    return df

def fetch_instructions_data(to_file_path: Optional[str] = None) -> Any:
    
    """
    Fetch Intruction Data Set
    """
    
    file_id = "13uAQeUAEe4y5X1MvqX57LPDSx_w_Vi1W"
    url = f"https://drive.google.com/uc?export=download&id={file_id}"

    resp = requests.get(url)
    resp.raise_for_status()

    data = resp.json()

    if to_file_path:
        with open(to_file_path, "w") as f:
            json.dump(data, f, indent=2)

    return data