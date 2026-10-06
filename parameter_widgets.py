"""Shared query parameter controls for advanced queries and customer workflows."""
import tkinter as tk
from tkinter import ttk


class ParameterChoice(ttk.Combobox):
    def __init__(self, parent, spec, variable, **kwargs):
        self.raw = variable
        self.labels = {item['label']: item['value'] for item in spec['options']}
        self.reverse = {value: label for label, value in self.labels.items()}
        self.display = tk.StringVar(master=parent)
        super().__init__(parent, textvariable=self.display, values=list(self.labels), state='readonly', **kwargs)
        self.bind('<<ComboboxSelected>>', self.selected)
        self.trace_id = self.raw.trace_add('write', self.sync)
        self.bind('<Destroy>', self.cleanup, add='+')
        self.sync()

    def selected(self, event=None):
        label = self.display.get()
        if label in self.labels:
            self.raw.set(self.labels[label])

    def sync(self, *args):
        self.display.set(self.reverse.get(self.raw.get(), '请选择…'))

    def cleanup(self, event):
        if event.widget is self and self.trace_id is not None:
            self.raw.trace_remove('write', self.trace_id)
            self.trace_id = None


def parameter_widget(parent, spec, variable, **kwargs):
    if 'options' in spec:
        kwargs.pop('show', None)
        return ParameterChoice(parent, spec, variable, **kwargs)
    return ttk.Entry(parent, textvariable=variable, **kwargs)
