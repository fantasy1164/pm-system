# gunicorn 自動載入本檔 (cwd = backend)。
# 關鍵:fork 不複製執行緒,備份迴圈必須在 worker 內啟動;
# 關機兜底掛在 worker 退出鉤子 (graceful shutdown 會經過這裡)。


def post_fork(server, worker):
    import app as appmod
    appmod.BACKUP.start(register_signals=False)


def _flush(reason):
    try:
        import app as appmod
        appmod.BACKUP.flush_if_dirty()
    except Exception as e:  # 兜底失敗不阻擋關機
        print(f"[gunicorn hook] flush ({reason}) 失敗: {e}")


def worker_int(worker):
    _flush("worker_int")


def worker_abort(worker):
    _flush("worker_abort")


def worker_exit(server, worker):
    _flush("worker_exit")
