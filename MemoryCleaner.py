import os
import sys
import stat
import ctypes
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

# ----------------------------------------------------------------------
# Проверка и запрос прав администратора
# ----------------------------------------------------------------------
def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except:
        return False

def run_as_admin():
    try:
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, " ".join(sys.argv), None, 1
        )
    except Exception as e:
        messagebox.showerror("Ошибка", f"Не удалось запросить права администратора:\n{e}")
        sys.exit(1)
    sys.exit(0)

# ----------------------------------------------------------------------
# Работа с файлами
# ----------------------------------------------------------------------
def collect_files_with_size(root_dirs):
    files = []
    total_size = 0
    for root_dir in root_dirs:
        root_path = Path(root_dir)
        if not root_path.exists():
            continue
        for file_path in root_path.rglob('*'):
            if file_path.is_file():
                files.append(str(file_path))
                try:
                    total_size += file_path.stat().st_size
                except OSError:
                    pass
    return files, total_size

def collect_empty_dirs(root_dirs):
    dirs = []
    for root_dir in root_dirs:
        root_path = Path(root_dir)
        if not root_path.exists():
            continue
        for dir_path in root_path.rglob('*'):
            if dir_path.is_dir():
                dirs.append(str(dir_path))
    dirs.sort(key=lambda x: len(x), reverse=True)
    return dirs

def delete_files_with_callback(files_list, callback):
    """
    Вызывает callback(i, total, current_file, deleted_count, deleted_size, processed_count)
    i - номер обработанного файла (начиная с 1)
    processed_count = i (всегда увеличивается)
    deleted_count - сколько реально удалено на данный момент
    """
    total = len(files_list)
    deleted_count = 0
    deleted_size = 0
    for i, filepath in enumerate(files_list, start=1):
        current_file = os.path.basename(filepath)
        file_size = 0
        try:
            file_size = os.path.getsize(filepath)
        except OSError:
            pass

        try:
            os.chmod(filepath, stat.S_IWRITE)
            os.remove(filepath)
            deleted_count += 1
            deleted_size += file_size
        except (PermissionError, OSError):
            pass  # файл занят или нет прав – пропускаем, но прогресс идёт

        callback(total, current_file, deleted_count, deleted_size, i)

    return deleted_count, deleted_size

def delete_empty_dirs(dirs_list):
    for dirpath in dirs_list:
        try:
            os.rmdir(dirpath)
        except OSError:
            pass

def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} Б"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.2f} КБ"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 ** 2):.2f} МБ"
    else:
        return f"{size_bytes / (1024 ** 3):.2f} ГБ"

# ----------------------------------------------------------------------
# Главное окно приложения
# ----------------------------------------------------------------------
class CacheCleanerApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Очистка системного кэша (все пользователи)")
        self.geometry("650x450")
        self.resizable(False, False)
        self.center_window()
        
        self.iconbitmap(sys.argv[0])

        self.total_files = 0
        self.total_size = 0
        self.deleted_files = 0
        self.deleted_size = 0
        self.processed_files = 0

        self.current_file_var = tk.StringVar(value="Ожидание начала...")
        self.percent_var = tk.StringVar(value="0%")
        self.processed_stats_var = tk.StringVar(value="Обработано: 0 | Всего: 0 | Осталось: 0")
        self.deleted_stats_var = tk.StringVar(value="Удалено файлов: 0 | Всего: 0 | (пропущено: 0)")
        self.size_stats_var = tk.StringVar(value="Удалено памяти: 0 Б | Всего памяти: 0 Б")
        self.dirs_info_var = tk.StringVar(value="Сбор информации о папках...")

        self.temp_windows = r"C:\Windows\Temp"
        self.user_temp_dirs = []        # список всех пользовательских Temp
        self.root_dirs_for_clean = []   # итоговый список (системная + пользовательские)

        self.create_widgets()

        if not is_admin():
            self.ask_admin_rights()
        else:
            self.scan_user_temp_dirs()

    def center_window(self):
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        x = (self.winfo_screenwidth() // 2) - (w // 2)
        y = (self.winfo_screenheight() // 2) - (h // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def create_widgets(self):
        # Рамка с информацией о папках
        info_frame = ttk.LabelFrame(self, text="Очищаемые папки", padding=5)
        info_frame.pack(fill=tk.X, padx=10, pady=5)
        self.dirs_label = ttk.Label(info_frame, textvariable=self.dirs_info_var, wraplength=600, justify=tk.LEFT)
        self.dirs_label.pack(anchor=tk.W)

        self.start_button = ttk.Button(self, text="Начать очистку", command=self.start_cleanup_thread)
        self.start_button.pack(pady=10)

        self.progress = ttk.Progressbar(self, orient=tk.HORIZONTAL, length=570, mode='determinate')
        self.progress.pack(pady=5, padx=10)

        percent_label = ttk.Label(self, textvariable=self.percent_var, font=("Arial", 12, "bold"))
        percent_label.pack()

        # Статистика удаления
        ttk.Label(self, text="Результат удаления:", font=("Arial", 9, "bold")).pack(anchor=tk.W, padx=10, pady=(5,0))
        deleted_label = ttk.Label(self, textvariable=self.deleted_stats_var, font=("Arial", 9))
        deleted_label.pack(anchor=tk.W, padx=20)

        # Статистика памяти
        ttk.Label(self, text="Память:", font=("Arial", 9, "bold")).pack(anchor=tk.W, padx=10, pady=(5,0))
        size_label = ttk.Label(self, textvariable=self.size_stats_var, font=("Arial", 9))
        size_label.pack(anchor=tk.W, padx=20)

        # Текущий файл
        ttk.Label(self, text="Текущий файл:").pack(anchor=tk.W, padx=10, pady=(15,0))
        current_file_label = ttk.Label(self, textvariable=self.current_file_var, foreground="blue", wraplength=580, justify=tk.LEFT)
        current_file_label.pack(fill=tk.X, padx=10, pady=2)

        self.status_var = tk.StringVar(value="Готов к работе.")
        status_bar = ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=5, pady=2)

    def scan_user_temp_dirs(self):
        # """Собирает все папки Temp для пользователей в C:\Users (исключая системные)"""
        users_path = Path("C:/Users")
        if not users_path.exists():
            self.dirs_info_var.set("Папка C:\\Users не найдена. Будет очищен только C:\\Windows\\Temp")
            self.user_temp_dirs = []
            return

        # Папки, которые не являются профилями реальных пользователей
        temp_list = []
        for user_dir in users_path.iterdir():
            if not user_dir.is_dir():
                continue
            temp_path = user_dir / "AppData" / "Local" / "Temp"
            if temp_path.exists():
                temp_list.append(str(temp_path))
        self.user_temp_dirs = temp_list
        if self.user_temp_dirs:
            info = f"Системная: {self.temp_windows}\n"
            info += f"Папки пользователей ({len(self.user_temp_dirs)} шт.):\n"
            for d in self.user_temp_dirs[:5]:  # показываем не более 5 для компактности
                info += f"  {d}\n"
            if len(self.user_temp_dirs) > 5:
                info += f"  ... и ещё {len(self.user_temp_dirs) - 5} папок"
            self.dirs_info_var.set(info)
        else:
            self.dirs_info_var.set(f"Системная: {self.temp_windows}\nПапки пользователей не найдены.")

    def ask_admin_rights(self):
        answer = messagebox.askyesno(
            "Требуются права администратора",
            "Для очистки временных файлов ВСЕХ пользователей и системной папки C:\\Windows\\Temp нужны права администратора.\n\n"
            "Хотите перезапустить программу с правами администратора?\n"
            "(Да – перезапуск, Нет – продолжит без прав, но будут очищены только доступные папки)"
        )
        if answer:
            run_as_admin()
        else:
            self.status_var.set("Отказ от прав. Системная и чужие папки не будут очищены.")
            # Всё равно соберём только те папки, куда есть доступ (обычно только свой Temp)
            current_user_temp = Path(os.environ.get('TEMP', ''))
            if current_user_temp.exists():
                self.user_temp_dirs = [str(current_user_temp)]
            else:
                self.user_temp_dirs = []
            self.update_dirs_info_after_admin_decline()

    def update_dirs_info_after_admin_decline(self):
        if self.user_temp_dirs:
            info = f"Системная: {self.temp_windows} (недоступна без прав)\n"
            info += f"Доступная папка пользователя:\n  {self.user_temp_dirs[0]}"
            self.dirs_info_var.set(info)
        else:
            self.dirs_info_var.set("Нет доступных папок для очистки.")

    def update_progress(self, total, current_file, deleted_count, deleted_size, processed_count):
        self.total_files = total
        self.deleted_files = deleted_count
        self.deleted_size = deleted_size
        self.processed_files = processed_count

        if total > 0:
            percent = (processed_count / total) * 100.0
        else:
            percent = 0.0

        skipped = processed_count - deleted_count
        remaining_size = max(0, self.total_size - deleted_size)

        def _update():
            self.progress['maximum'] = total
            self.progress['value'] = processed_count
            self.percent_var.set(f"{percent:.1f}%")
            self.deleted_stats_var.set(
                f"Удалено файлов: {deleted_count} | Всего: {total} | Пропущено (заняты/нет прав): {skipped}"
            )
            self.size_stats_var.set(
                f"Удалено памяти: {format_size(deleted_size)} | Всего во временных файлах: {format_size(self.total_size)} | Осталось: {format_size(remaining_size)}"
            )
            self.current_file_var.set(current_file)
            self.status_var.set(f"Обработка: {current_file}")
        self.after(0, _update)

    def finish_cleanup(self, deleted_count, deleted_size, total_count, total_size):
        def _finish():
            self.start_button.config(state=tk.NORMAL)
            self.status_var.set(f"Завершено. Удалено {deleted_count} файлов ({format_size(deleted_size)}).")
            messagebox.showinfo(
                "Успех",
                f"Очистка кэша завершена!\n\n"
                f"Всего файлов: {total_count}\n"
                f"Удалено: {deleted_count}\n"
                f"Пропущено (заняты/нет прав): {total_count - deleted_count}\n"
                f"Освобождено памяти: {format_size(deleted_size)}\n"
                f"Общий размер всех временных файлов: {format_size(total_size)}"
            )
        self.after(0, _finish)

    def cleanup_task(self):
        try:
            # Формируем итоговый список папок для очистки
            dirs_to_clean = []
            if os.path.exists(self.temp_windows):
                dirs_to_clean.append(self.temp_windows)
            for user_temp in self.user_temp_dirs:
                if os.path.exists(user_temp):
                    dirs_to_clean.append(user_temp)

            if not dirs_to_clean:
                self.after(0, lambda: self.status_var.set("Нет доступных папок для очистки."))
                self.after(0, lambda: self.start_button.config(state=tk.NORMAL))
                return

            self.after(0, lambda: self.status_var.set("Сбор файлов и расчёт размера..."))
            files_to_delete, total_size = collect_files_with_size(dirs_to_clean)
            self.total_size = total_size
            total_files = len(files_to_delete)
            self.total_files = total_files

            if total_files == 0:
                self.after(0, lambda: messagebox.showinfo("Информация", "Нет файлов для удаления."))
                self.after(0, lambda: self.start_button.config(state=tk.NORMAL))
                return

            deleted_cnt, deleted_sz = delete_files_with_callback(
                files_to_delete,
                lambda total, curr_file, del_cnt, del_sz, proc_cnt:
                    self.update_progress(total, curr_file, del_cnt, del_sz, proc_cnt)
            )

            # Удаляем пустые папки
            all_dirs = collect_empty_dirs(dirs_to_clean)
            delete_empty_dirs(all_dirs)

            self.finish_cleanup(deleted_cnt, deleted_sz, total_files, total_size)

        except Exception as e:
            err_msg = f"Ошибка: {e}"
            self.after(0, lambda: messagebox.showerror("Ошибка", err_msg))
            self.after(0, lambda: self.start_button.config(state=tk.NORMAL))
            self.after(0, lambda: self.status_var.set("Ошибка очистки."))

    def start_cleanup_thread(self):
        self.start_button.config(state=tk.DISABLED)
        self.progress['value'] = 0
        self.percent_var.set("0%")
        self.deleted_stats_var.set("Удалено файлов: 0 | Всего: 0 | Пропущено: 0")
        self.size_stats_var.set("Удалено памяти: 0 Б | Всего: 0 Б | Осталось: 0 Б")
        self.current_file_var.set("Подготовка...")
        self.status_var.set("Сбор файлов...")

        # Если администратор отказался от прав, сканируем папки заново
        if not is_admin():
            self.scan_user_temp_dirs()
        else:
            # При наличии прав – собираем всех пользователей заново (на случай создания новых)
            self.scan_user_temp_dirs()

        thread = threading.Thread(target=self.cleanup_task, daemon=True)
        thread.start()

# ----------------------------------------------------------------------
if __name__ == "__main__":
    app = CacheCleanerApp()
    app.mainloop()