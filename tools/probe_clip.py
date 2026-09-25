import os
import sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.clip_model import CLIPModel


if __name__ == "__main__":

    print("正在加载CLIP模型...")

    model = CLIPModel()

    print("CLIP加载成功!")
    print("运行设备:", model.device)