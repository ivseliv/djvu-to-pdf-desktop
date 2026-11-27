"""A native, offline desktop shell for preserving DjVu text layers in PDF."""

from __future__ import annotations

import queue
import subprocess
import threading
import tkinter as tk
from collections.abc import Callable  # ruff: ignore[typing-only-standard-library-import]
from contextlib import suppress
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from . import __version__
from .core import ConversionSettings, build_command, default_output_path
from .jobs import JobBusyError, job_size, lock_job, read_manifest, recoverable_jobs, remove_job
from .runner import ConversionRunner


BACKGROUND = '#F4F6FA'
SURFACE = '#FFFFFF'
INK = '#17212B'
MUTED = '#657181'
ACCENT = '#356AE6'
ACCENT_DARK = '#2856BD'
BORDER = '#DDE3EC'

MODE_LABELS = {
    'Автоматически — рекомендовано': 'infer',
    'Чёрно-белый — компактный PDF': 'bitonal',
    'Оттенки серого': 'grayscale',
    'Цвет': 'rgb',
}
MODE_HINTS = {
    'infer': 'Конвертер сам определит подходящий тип для каждой страницы. Лучший выбор в большинстве случаев.',
    'bitonal': 'Для чёрно-белых сканов. Обычно даёт самый компактный PDF.',
    'grayscale': 'Для серых сканов без цветных иллюстраций.',
    'rgb': 'Для книг с цветными иллюстрациями и фотографиями.',
}
PROFILE_LABELS = {
    'Сбалансированный — 300 dpi, цвет': (300, 'infer', 40),
    'Компактный — 250 dpi, оттенки серого': (250, 'grayscale', 35),
    'Чёрно-белый — 300 dpi, минимальный размер': (300, 'bitonal', 50),
    'Оригинальное разрешение — большой файл': (None, 'infer', 50),
}
RESOLUTION_LABELS = {
    'Оригинальное': None,
    '600 dpi': 600,
    '400 dpi': 400,
    '300 dpi': 300,
    '250 dpi': 250,
    '200 dpi': 200,
    '150 dpi': 150,
}


class ConverterApp(ttk.Frame):
    """A polished single-document converter with an optional advanced panel."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._configure_window()
        self.canvas = tk.Canvas(root, background=BACKGROUND, highlightthickness=0, borderwidth=0)
        self.scrollbar = ttk.Scrollbar(root, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        super().__init__(self.canvas, style='App.TFrame', padding=(30, 26))
        self.canvas_window = self.canvas.create_window((0, 0), window=self, anchor='nw')
        self.bind('<Configure>', self._update_scroll_region)
        self.canvas.bind('<Configure>', self._fit_content_width)
        self.canvas.bind_all('<MouseWheel>', self._scroll)

        self.source = tk.StringVar()
        self.output = tk.StringVar()
        self.keep_text = tk.BooleanVar(value=True)
        self.overwrite = tk.BooleanVar(value=True)
        self.mode_label = tk.StringVar(value='Автоматически — рекомендовано')
        self.profile_label = tk.StringVar(value='Сбалансированный — 300 dpi, цвет')
        self.resolution_label = tk.StringVar(value='300 dpi')
        self.quality = tk.IntVar(value=40)
        self.pool_size = tk.StringVar(value='')
        self.show_advanced = tk.BooleanVar(value=False)
        self.show_log = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value='Выберите DjVu-файл, чтобы начать.')
        self.progress_value = tk.DoubleVar(value=0)
        self.progress_text = tk.StringVar(value='')
        self.page_progress = 0.0
        self.mode_hint = tk.StringVar(value=MODE_HINTS['infer'])
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.runner = ConversionRunner(self.events)
        self.running = False
        self.closing = False

        self._build()
        self.root.protocol('WM_DELETE_WINDOW', self._close)
        self.after(100, self._drain_events)
        self.after(300, self._offer_recovery)

    def _update_scroll_region(self, _event: object | None = None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def _fit_content_width(self, event: tk.Event[tk.Misc]) -> None:
        self.canvas.itemconfigure(self.canvas_window, width=event.width)

    def _scroll(self, event: tk.Event[tk.Misc]) -> None:
        if event.delta:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')

    def _configure_window(self) -> None:
        self.root.title('DjVu → PDF')
        self.root.geometry('840x650')
        self.root.minsize(720, 540)
        self.root.configure(background=BACKGROUND)
        style = ttk.Style(self.root)
        with suppress(tk.TclError):
            style.theme_use('clam')
        style.configure('App.TFrame', background=BACKGROUND)
        style.configure('Card.TFrame', background=SURFACE, relief='flat')
        style.configure('Info.TFrame', background='#EDF3FF')
        style.configure('Header.TLabel', background=BACKGROUND, foreground=INK, font=('Helvetica', 25, 'bold'))
        style.configure('Subtitle.TLabel', background=BACKGROUND, foreground=MUTED, font=('Helvetica', 11))
        style.configure('Section.TLabel', background=SURFACE, foreground=INK, font=('Helvetica', 12, 'bold'))
        style.configure('Field.TLabel', background=SURFACE, foreground=INK, font=('Helvetica', 10, 'bold'))
        style.configure('Body.TLabel', background=SURFACE, foreground=MUTED, font=('Helvetica', 10))
        style.configure('Info.TLabel', background='#EDF3FF', foreground='#31508A', font=('Helvetica', 10))
        style.configure('Hint.TLabel', background=SURFACE, foreground=MUTED, font=('Helvetica', 9))
        style.configure('Status.TLabel', background=BACKGROUND, foreground=MUTED, font=('Helvetica', 10))
        style.configure('TEntry', fieldbackground='#FFFFFF', bordercolor=BORDER, padding=(9, 8))
        style.configure('TCombobox', fieldbackground='#FFFFFF', bordercolor=BORDER, padding=(7, 6))
        style.configure('Primary.TButton', background=ACCENT, foreground='#FFFFFF', borderwidth=0, padding=(18, 10), font=('Helvetica', 11, 'bold'))
        style.map('Primary.TButton', background=[('active', ACCENT_DARK), ('disabled', '#AAB9DF')])
        style.configure('Secondary.TButton', background='#E9EEF7', foreground='#2C3B4B', borderwidth=0, padding=(14, 9))
        style.map('Secondary.TButton', background=[('active', '#DDE5F2')])
        style.configure('TCheckbutton', background=SURFACE, foreground=INK, font=('Helvetica', 10))
        self._install_menu()

    def _install_menu(self) -> None:
        menu_bar = tk.Menu(self.root)
        app_menu = tk.Menu(menu_bar, name='apple', tearoff=False)
        app_menu.add_command(label='О программе DjVu → PDF', command=self._show_about)
        menu_bar.add_cascade(label='DjVu → PDF', menu=app_menu)
        self.root.configure(menu=menu_bar)

    def _show_about(self) -> None:
        messagebox.showinfo(
            'О программе',
            f'DjVu → PDF\nВерсия {__version__}\n\n'
            'Автономный конвертер DjVu в PDF\n'
            'с сохранением существующего текстового слоя.',
            parent=self.root,
        )

    def _build(self) -> None:
        ttk.Label(self, text='DjVu → PDF', style='Header.TLabel').pack(anchor='w')
        ttk.Label(self, text='Автономный конвертер с сохранением текстового слоя и оглавления.', style='Subtitle.TLabel').pack(anchor='w', pady=(5, 20))

        source_card = ttk.Frame(self, style='Card.TFrame', padding=18)
        source_card.pack(fill='x')
        source_card.columnconfigure(0, weight=1)
        ttk.Label(source_card, text='Файлы', style='Section.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(source_card, text='Оригинал не меняется — будет создан новый PDF.', style='Body.TLabel').grid(row=1, column=0, sticky='w', pady=(3, 14))
        self._file_field(source_card, 2, 'Исходный DjVu', self.source, self._choose_source, 'Выбрать файл')
        self._file_field(source_card, 3, 'Готовый PDF', self.output, self._choose_output, 'Куда сохранить')

        info = ttk.Frame(self, style='Info.TFrame', padding=(14, 11))
        info.pack(fill='x', pady=(14, 0))
        ttk.Label(info, text='✓ Текстовый слой и закладки переносятся из исходного DjVu, если в нём они есть.', style='Info.TLabel').pack(anchor='w')

        primary_options = ttk.Frame(self, style='Card.TFrame', padding=(18, 14))
        primary_options.pack(fill='x', pady=(14, 0))
        ttk.Label(primary_options, text='Профиль размера и качества', style='Field.TLabel').pack(anchor='w')
        profile_picker = ttk.Combobox(
            primary_options,
            textvariable=self.profile_label,
            values=tuple(PROFILE_LABELS),
            state='readonly',
        )
        profile_picker.pack(fill='x', pady=(6, 12))
        profile_picker.bind('<<ComboboxSelected>>', self._apply_profile)
        ttk.Checkbutton(primary_options, text='Сохранить текстовый слой для поиска и копирования', variable=self.keep_text).pack(anchor='w')
        ttk.Checkbutton(primary_options, text='Заменять PDF, если файл с таким именем уже есть', variable=self.overwrite).pack(anchor='w', pady=(8, 0))

        self.advanced_toggle = ttk.Checkbutton(self, text='Расширенные настройки', variable=self.show_advanced, command=self._toggle_advanced)
        self.advanced_toggle.pack(anchor='w', pady=(16, 8))

        self.advanced_card = ttk.Frame(self, style='Card.TFrame', padding=18)
        self.advanced_card.columnconfigure(1, weight=1)
        ttk.Label(self.advanced_card, text='Настройки изображения и скорости', style='Section.TLabel').grid(row=0, column=0, columnspan=2, sticky='w')
        ttk.Label(self.advanced_card, text='Тип страниц', style='Field.TLabel').grid(row=1, column=0, sticky='nw', pady=(16, 0))
        mode_area = ttk.Frame(self.advanced_card, style='Card.TFrame')
        mode_area.grid(row=1, column=1, sticky='ew', pady=(13, 0))
        mode_area.columnconfigure(0, weight=1)
        mode_picker = ttk.Combobox(mode_area, textvariable=self.mode_label, values=tuple(MODE_LABELS), state='readonly')
        mode_picker.grid(row=0, column=0, sticky='ew')
        mode_picker.bind('<<ComboboxSelected>>', self._update_mode_hint)
        ttk.Label(mode_area, textvariable=self.mode_hint, style='Hint.TLabel', wraplength=470).grid(row=1, column=0, sticky='w', pady=(5, 0))

        ttk.Label(self.advanced_card, text='Разрешение', style='Field.TLabel').grid(row=2, column=0, sticky='nw', pady=(18, 0))
        resolution_picker = ttk.Combobox(
            self.advanced_card,
            textvariable=self.resolution_label,
            values=tuple(RESOLUTION_LABELS),
            state='readonly',
        )
        resolution_picker.grid(row=2, column=1, sticky='ew', pady=(15, 0))

        ttk.Label(self.advanced_card, text='Качество JPEG', style='Field.TLabel').grid(row=3, column=0, sticky='nw', pady=(18, 0))
        quality_area = ttk.Frame(self.advanced_card, style='Card.TFrame')
        quality_area.grid(row=3, column=1, sticky='ew', pady=(15, 0))
        quality_area.columnconfigure(0, weight=1)
        ttk.Scale(quality_area, from_=1, to=100, variable=self.quality).grid(row=0, column=0, sticky='ew')
        ttk.Label(quality_area, textvariable=self.quality, style='Field.TLabel', width=3).grid(row=0, column=1, padx=(10, 0))
        ttk.Label(quality_area, text='Влияет только на цветные и серые страницы.', style='Hint.TLabel').grid(row=1, column=0, columnspan=2, sticky='w', pady=(5, 0))

        ttk.Label(self.advanced_card, text='Параллельность', style='Field.TLabel').grid(row=4, column=0, sticky='nw', pady=(18, 0))
        workers_area = ttk.Frame(self.advanced_card, style='Card.TFrame')
        workers_area.grid(row=4, column=1, sticky='w', pady=(15, 0))
        ttk.Spinbox(workers_area, from_=1, to=64, textvariable=self.pool_size, width=6).pack(side='left')
        ttk.Label(workers_area, text='Пустое поле — автоматический выбор', style='Hint.TLabel').pack(side='left', padx=(10, 0))

        ttk.Button(
            self.advanced_card,
            text='Очистить временные файлы…',
            style='Secondary.TButton',
            command=self._clean_jobs,
        ).grid(row=5, column=1, sticky='w', pady=(18, 0))

        actions = ttk.Frame(self, style='App.TFrame')
        actions.pack(fill='x', pady=(18, 0))
        self.convert_button = ttk.Button(actions, text='Конвертировать в PDF', style='Primary.TButton', command=self._start)
        self.convert_button.pack(side='left')
        self.cancel_button = ttk.Button(actions, text='Отменить', style='Secondary.TButton', command=self._cancel, state='disabled')
        self.cancel_button.pack(side='left', padx=(9, 0))
        ttk.Label(actions, textvariable=self.status, style='Status.TLabel').pack(side='right')

        self.progress_area = ttk.Frame(self, style='App.TFrame')
        self.progress_area.pack(fill='x', pady=(12, 0))
        self.progress_area.columnconfigure(0, weight=1)
        self.progress_bar = ttk.Progressbar(
            self.progress_area,
            variable=self.progress_value,
            maximum=100,
            mode='determinate',
        )
        self.progress_bar.grid(row=0, column=0, sticky='ew')
        ttk.Label(self.progress_area, textvariable=self.progress_text, style='Status.TLabel').grid(
            row=1, column=0, sticky='w', pady=(5, 0),
        )
        self.progress_area.pack_forget()

        self.log_toggle = ttk.Checkbutton(self, text='Показать журнал конвертации', variable=self.show_log, command=self._toggle_log)
        self.log_toggle.pack(anchor='w', pady=(18, 7))
        self.log_card = ttk.Frame(self, style='Card.TFrame', padding=10)
        self.log = tk.Text(self.log_card, height=9, wrap='word', state='disabled', borderwidth=0, background='#F9FAFC', foreground='#334155', font=('Menlo', 10))
        self.log.pack(fill='both', expand=True)

    @staticmethod
    def _file_field(parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, command: Callable[[], Any], button: str) -> None:
        ttk.Label(parent, text=label, style='Field.TLabel').grid(row=row * 2, column=0, sticky='w', pady=(0, 5))
        field = ttk.Frame(parent, style='Card.TFrame')
        field.grid(row=row * 2 + 1, column=0, sticky='ew', pady=(0, 13))
        field.columnconfigure(0, weight=1)
        ttk.Entry(field, textvariable=variable).grid(row=0, column=0, sticky='ew')
        ttk.Button(field, text=button, style='Secondary.TButton', command=command).grid(row=0, column=1, padx=(9, 0))

    def _toggle_advanced(self) -> None:
        if self.show_advanced.get():
            self.advanced_card.pack(fill='x', before=self.advanced_toggle)
            self.advanced_toggle.configure(text='Скрыть расширенные настройки')
        else:
            self.advanced_card.pack_forget()
            self.advanced_toggle.configure(text='Расширенные настройки')

    def _toggle_log(self) -> None:
        if self.show_log.get():
            self.log_card.pack(fill='both', expand=True)
            self.log_toggle.configure(text='Скрыть журнал конвертации')
        else:
            self.log_card.pack_forget()
            self.log_toggle.configure(text='Показать журнал конвертации')

    def _update_mode_hint(self, _event: object | None = None) -> None:
        self.mode_hint.set(MODE_HINTS[MODE_LABELS[self.mode_label.get()]])

    def _apply_profile(self, _event: object | None = None) -> None:
        max_dpi, mode, quality = PROFILE_LABELS[self.profile_label.get()]
        self.resolution_label.set('Оригинальное' if max_dpi is None else f'{max_dpi} dpi')
        self.mode_label.set(next(label for label, value in MODE_LABELS.items() if value == mode))
        self.quality.set(quality)
        self.mode_hint.set(MODE_HINTS[mode])

    def _choose_source(self) -> None:
        chosen = filedialog.askopenfilename(title='Выберите DjVu-файл', filetypes=(('DjVu', '*.djvu *.djv'), ('Все файлы', '*')))
        if chosen:
            source = Path(chosen)
            if str(source) != self.source.get() or not self.output.get():
                self.output.set(str(default_output_path(source)))
            self.source.set(str(source))
            self.status.set('Готово к конвертации.')

    def _choose_output(self) -> None:
        initial = self.output.get() or 'converted.pdf'
        chosen = filedialog.asksaveasfilename(title='Сохранить PDF', initialfile=Path(initial).name, defaultextension='.pdf', filetypes=(('PDF', '*.pdf'),))
        if chosen:
            self.output.set(chosen)

    def _settings(self) -> ConversionSettings:
        pool_text = self.pool_size.get().strip()
        try:
            pool_size = int(pool_text) if pool_text else None
        except ValueError as error:
            raise ValueError('Число процессов должно быть целым числом.') from error
        return ConversionSettings(source=Path(self.source.get()), output=Path(self.output.get()), preserve_text=self.keep_text.get(), image_mode=MODE_LABELS[self.mode_label.get()], quality=self.quality.get(), max_dpi=RESOLUTION_LABELS[self.resolution_label.get()], pool_size=pool_size, overwrite=self.overwrite.get(), stream_output=True)

    def _validated_start_settings(self) -> tuple[ConversionSettings, list[str]]:
        settings = self._settings()
        if not settings.source.is_file():
            raise ValueError('Выберите существующий DjVu-файл.')
        if settings.output.exists():
            if settings.source.samefile(settings.output):
                raise ValueError('Выберите другой путь для PDF: он совпадает с исходным DjVu.')
            if not settings.overwrite:
                raise ValueError('PDF с таким именем уже существует.')
        return settings, build_command(settings)

    def _start(self) -> None:
        try:
            settings, command = self._validated_start_settings()
        except (ValueError, OSError) as error:
            messagebox.showerror('Не удалось начать', str(error), parent=self.root)
            return
        self._append('$ ' + subprocess.list2cmdline(command) + '\n')
        self.status.set('Идёт конвертация…')
        self.progress_value.set(0)
        self.page_progress = 0.0
        self.progress_text.set('Подготовка…')
        self.progress_area.pack(fill='x', pady=(12, 0), before=self.log_toggle)
        self.convert_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.runner.cancel_requested.clear()
        self.running = True
        threading.Thread(target=self.runner.run, args=(settings,), daemon=True).start()

    def _cancel(self) -> None:
        self.status.set('Отмена…')
        self.runner.cancel()

    def _close(self) -> None:
        if self.running:
            if not messagebox.askyesno('Прервать конвертацию?', 'Конвертация ещё идёт. Прервать её и закрыть приложение?', parent=self.root):
                return
            self._cancel()
            self.closing = True
            return
        self.root.destroy()

    def _offer_recovery(self) -> None:
        if self.running:
            return
        jobs = recoverable_jobs()
        if not jobs:
            return
        job = jobs[0]
        manifest = read_manifest(job) or {}
        size = job_size(job)
        answer = messagebox.askyesnocancel(
            'Незавершённая конвертация',
            f'Найдены временные данные ({size / 1024**3:.1f} ГБ).\n\n'
            'Да — продолжить последнее задание.\nНет — удалить все временные задания.\nОтмена — оставить как есть.',
            parent=self.root,
        )
        if answer is True:
            self.runner.resume_job = job
            self.source.set(str(manifest.get('source', '')))
            self.output.set(str(manifest.get('output', '')))
            self.keep_text.set(bool(manifest.get('preserve_text', True)))
            self.overwrite.set(bool(manifest.get('overwrite', True)))
            mode = str(manifest.get('image_mode', 'infer'))
            for label, value in MODE_LABELS.items():
                if value == mode:
                    self.mode_label.set(label)
                    break
            self.quality.set(int(str(manifest.get('quality', 50))))
            max_dpi = manifest.get('max_dpi')
            self.resolution_label.set('Оригинальное' if max_dpi is None else f'{int(str(max_dpi))} dpi')
            pool_size = manifest.get('pool_size')
            self.pool_size.set('' if pool_size is None else str(pool_size))
            self.status.set('Незавершённое задание готово к продолжению.')
        elif answer is False:
            self._remove_inactive_jobs(jobs)
            self.status.set('Временные задания удалены.')

    def _clean_jobs(self) -> None:
        jobs = recoverable_jobs()
        if not jobs:
            messagebox.showinfo('Временные файлы', 'Временных заданий нет.', parent=self.root)
            return
        total = sum(job_size(job) for job in jobs)
        if messagebox.askyesno(
            'Удалить временные файлы?',
            f'Будет удалено заданий: {len(jobs)}\nОбщий размер: {total / 1024**3:.1f} ГБ',
            parent=self.root,
        ):
            self._remove_inactive_jobs(jobs)
            self.runner.resume_job = None
            self.status.set('Временные файлы удалены.')

    @staticmethod
    def _remove_inactive_jobs(jobs: list[Path]) -> None:
        for job in jobs:
            with suppress(JobBusyError, FileNotFoundError), lock_job(job):
                remove_job(job)

    # ruff: ignore[complex-structure]
    def _drain_events(self) -> None:
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'log':
                    self._append(str(value))
                elif kind == 'status':
                    self.status.set(str(value))
                elif kind == 'progress':
                    completed, total = value
                    self.page_progress = 15 + (60 * completed / total if total else 0)
                    self.progress_value.set(max(self.progress_value.get(), self.page_progress))
                    self.status.set('Обрабатываю страницы…')
                    self.progress_text.set(f'Страницы: {completed:,} из {total:,} · {100 * completed / total:.1f}%')
                elif kind == 'combine':
                    completed, total = value
                    chunk_total = max(0, total - 2)
                    if completed <= chunk_total:
                        self.status.set('Собираю блоки PDF…')
                        self.progress_text.set(f'Блоки: {completed:,} из {chunk_total:,}')
                        if completed == chunk_total:
                            self.progress_value.set(max(self.progress_value.get(), 75))
                    elif completed == chunk_total + 1:
                        self.status.set('Проверяю готовый PDF…')
                        self.progress_text.set('Проверка структуры, страниц и текстового слоя…')
                        self.progress_value.set(max(self.progress_value.get(), 90))
                    else:
                        self.status.set('Проверяю готовый PDF…')
                        self.progress_text.set('Проверка структуры PDF…')
                        self.progress_value.set(max(self.progress_value.get(), 95))
                elif kind == 'transfer':
                    phase, completed, total = value
                    if phase == 'prepare':
                        self.status.set('Подготавливаю DjVu для конвертации…')
                        self.progress_value.set(max(self.progress_value.get(), 5 * completed / total if total else 0))
                        self.progress_text.set(f'Копирование DjVu: {completed / 1024**2:.0f} из {total / 1024**2:.0f} МБ')
                    else:
                        self.status.set('Сохраняю готовый PDF…')
                        self.progress_value.set(95 + (5 * completed / total if total else 0))
                        self.progress_text.set(f'Сохранение: {100 * completed / total:.1f}%')
                elif kind == 'qpdf':
                    percent = int(value)
                    self.status.set('Объединяю PDF…')
                    self.progress_value.set(max(self.progress_value.get(), 75 + 15 * percent / 100))
                    self.progress_text.set(f'Потоковое объединение: {percent}%')
                elif kind == 'text-progress':
                    completed, total = value
                    self.status.set('Переношу текстовый слой…')
                    self.progress_value.set(max(self.progress_value.get(), 5 + (10 * completed / total if total else 0)))
                    self.progress_text.set(f'Текстовый слой: {completed:,} из {total:,}')
                elif kind == 'validation':
                    elapsed = int(value)
                    self.status.set('Проверяю готовый PDF…')
                    self.progress_value.set(max(self.progress_value.get(), 90))
                    self.progress_text.set(f'Полная проверка PDF выполняется · {elapsed // 60}:{elapsed % 60:02d}')
                else:
                    return_code = int(value)
                    self.running = False
                    self.convert_button.configure(state='normal')
                    self.cancel_button.configure(state='disabled')
                    if self.closing:
                        self.root.destroy()
                        return
                    if return_code == 0:
                        self.progress_value.set(100)
                        self.progress_text.set('Готово · 100%')
                        self.status.set('Готово — PDF создан.')
                        self._append('\nКонвертация завершена.\n')
                    elif self.runner.cancel_requested.is_set():
                        self.status.set('Конвертация отменена.')
                        self.progress_text.set('Отменено')
                    else:
                        self.status.set('Конвертация не завершилась.')
                        self._append(f'\nПроцесс завершился с кодом {return_code}.\n')
                        messagebox.showerror(
                            'Конвертация не завершилась',
                            'PDF не был создан. Исходный DjVu не менялся; '
                            'подробности есть в журнале конвертации.',
                            parent=self.root,
                        )
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _append(self, text: str) -> None:
        self.log.configure(state='normal')
        self.log.insert('end', text)
        self.log.see('end')
        self.log.configure(state='disabled')


def main() -> None:
    root = tk.Tk()
    ConverterApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
