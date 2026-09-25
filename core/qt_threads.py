# core/qt_threads.py
"""QThread 回收助手：等待线程真正退出后再丢引用。

背景（2026-09-25 实测）：worker 的完成信号在主线程收到时，run() 可能尚未
返回；此时把唯一引用置 None，QThread 会在「仍在运行」状态下被析构，
导致进程退出挂起（实测 492s → 加回收后 1.5s）。

约定：所有 worker 的 done/failed 回调，在清引用前先 reap_thread(worker)。
纯 duck-typing，不 import Qt（便于测试与无 Qt 环境）。
"""


def reap_thread(thread, timeout_ms=3000):
    """等待 thread 退出；None / 已结束 / 异常都安全。返回是否已停止。"""
    try:
        if thread is None:
            return True
        if thread.isRunning():
            thread.wait(int(timeout_ms))
        return not thread.isRunning()
    except Exception:
        return True
