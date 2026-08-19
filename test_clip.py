from core.clip_model import CLIPModel


if __name__ == "__main__":

    print("正在加载CLIP模型...")

    model = CLIPModel()

    print("CLIP加载成功!")
    print("运行设备:", model.device)