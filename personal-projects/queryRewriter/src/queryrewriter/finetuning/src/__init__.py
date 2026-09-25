"""
datasets>=2.20.0 transformers>=4.51.0 peft>=0.14.0 accelerate>=0.34.0 bitsandbytes>=0.45.0 scikit-learn>=1.5.0 sentence-transformers>=3.0.0 pyyaml>=6.0.2 orjson>=3.10.0

pip install -r requirements.txt
git clone https://github.com/hiyouga/LLaMA-Factory.git
cd LLaMA-Factory
pip install -e ".[torch,metrics]"  # à lancer depuis le repo LLaMA-Factory
bash scripts/split_data.sh
bash scripts/train.sh

"""