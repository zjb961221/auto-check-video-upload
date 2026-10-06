"""Keep result polling alive after a rendering error; never replay an operation."""

def guarded_poll(widget, consume, recover):
    try:
        consume()
    except Exception as exc:
        recover(exc)
    finally:
        if widget.winfo_exists():
            widget.poll_id = widget.after(100, widget.poll)
