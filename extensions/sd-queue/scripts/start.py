import sys
from fastapi import FastAPI

import gradio as gr


def start_queue(_: gr.Blocks, app: FastAPI):
    if '--start-task-listener' in sys.argv:
        from scripts.TaskListener import TaskListener
        print(f"Launching API server with task listener")
        task = TaskListener(app)
        task.start()


try:
    import modules.script_callbacks as script_callbacks

    script_callbacks.on_app_started(start_queue)
except:
    pass
