import os
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
import pandas as pd
import zipfile
import winreg
import re
from PIL import Image, ImageTk
import ctypes

# --- 全局常量 ---
CONFIG_FILE = "pdps_master_config_v13.xlsx"
REG_PATH = r"Software\TECNOMATIX\TUNE\NewAssembler\Options\EMS"


# ==========================================
# 页面模块 1：项目管理 (核心路径解析功能)
# ==========================================
class ProjectManagerPage(tk.Frame):
    def __init__(self, parent, root):
        super().__init__(parent)
        self.root = root  # 保存根窗口引用
        self.search_paths = []
        self.df_data = pd.DataFrame(
            columns=["Company", "Plant", "Area", "Project Name", "Project Code", "File Name", "Full Path",
                     "System Root"])
        self.current_preview_image = None

        # 搜索与筛选变量
        self.search_var = tk.StringVar()
        self.filter_company_var = tk.StringVar(value="全部公司")

        self.setup_ui()
        self.setup_tree_context_menu()  # 修复：调用右键菜单设置
        self.load_local_config()

    def setup_ui(self):
        # --- 1. 顶部搜索与筛选栏 ---
        filter_frame = tk.Frame(self, pady=8, padx=10, bg="#34495e")
        filter_frame.pack(fill=tk.X)

        tk.Label(filter_frame, text="🔍 搜索项目:", fg="white", bg="#34495e").pack(side=tk.LEFT, padx=5)
        search_entry = tk.Entry(filter_frame, textvariable=self.search_var, width=30)
        search_entry.pack(side=tk.LEFT, padx=5)
        self.search_var.trace_add("write", lambda *args: self.refresh_tree())

        tk.Label(filter_frame, text="🏢 公司筛选:", fg="white", bg="#34495e").pack(side=tk.LEFT, padx=(20, 5))
        self.combo_company = ttk.Combobox(filter_frame, textvariable=self.filter_company_var, state="readonly",
                                          width=15)
        self.combo_company.pack(side=tk.LEFT)
        self.combo_company.bind("<<ComboboxSelected>>", lambda e: self.refresh_tree())

        tk.Button(filter_frame, text="💾 保存修改", command=self.save_config, bg="#f1c40f", fg="white", padx=10).pack(
            side=tk.RIGHT, padx=5)
        tk.Button(filter_frame, text="🔄 深度扫描", command=self.scan_files, bg="#27ae60", fg="white", padx=10).pack(
            side=tk.RIGHT, padx=5)
        tk.Button(filter_frame, text="＋ 添加目录", command=self.add_search_folder, bg="#3498db", fg="white",
                  padx=10).pack(side=tk.RIGHT, padx=5)
        tk.Button(filter_frame, text="打开Catia", command=self.open_catia, bg="#3498db", fg="white",
                  padx=10).pack(side=tk.RIGHT, padx=5)

        # --- 2. 主体布局 (左侧树，右侧详情) ---
        main_pane = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashrelief=tk.RAISED, sashwidth=4)
        main_pane.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 左侧列表
        left_frame = tk.Frame(main_pane)
        main_pane.add(left_frame, width=900)

        self.tree = ttk.Treeview(
            left_frame,
            columns=("Code", "Status", "FilePath", "FileName", "FileSize", "Modified"),
            show="tree headings",
            height=25
        )

        # 设置各列标题
        self.tree.heading("#0", text="组织架构", anchor="w")
        self.tree.heading("Code", text="项目代号")
        self.tree.heading("Status", text="状态")
        self.tree.heading("FilePath", text="文件路径")
        self.tree.heading("FileName", text="文件名")
        self.tree.heading("FileSize", text="大小")
        self.tree.heading("Modified", text="修改时间")

        # 设置列宽度
        self.tree.column("#0", width=300, minwidth=200, stretch=False)
        self.tree.column("Code", width=80, minwidth=60, stretch=False)
        self.tree.column("Status", width=60, minwidth=50, stretch=False)
        self.tree.column("FilePath", width=200, minwidth=150, stretch=True)
        self.tree.column("FileName", width=150, minwidth=100, stretch=False)
        self.tree.column("FileSize", width=80, minwidth=60, stretch=False)
        self.tree.column("Modified", width=120, minwidth=100, stretch=False)

        scrollbar = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_item_select)
        self.tree.bind("<Double-1>", lambda e: self.launch_pdps())

        # 右侧面板
        right_frame = tk.Frame(main_pane, bg="#ecf0f1", padx=15)
        main_pane.add(right_frame)

        # 预览图
        tk.Label(right_frame, text="项目预览", font=("微软雅黑", 10, "bold"), bg="#ecf0f1").pack(anchor="w",
                                                                                                 pady=(5, 2))
        self.preview_container = tk.Frame(right_frame, width=250, height=220, bg="black")
        self.preview_container.pack_propagate(False)
        self.preview_container.pack(fill=tk.X, pady=5)
        self.lbl_image = tk.Label(self.preview_container, bg="black")
        self.lbl_image.pack(expand=True)

        # 输入字段
        self.entries = {}
        fields = [("所属公司:", "Company"), ("所在厂房:", "Plant"), ("所属区域:", "Area"),
                  ("项目名称:", "Project Name"), ("项目代号:", "Project Code")]
        for label, key in fields:
            tk.Label(right_frame, text=label, bg="#ecf0f1").pack(anchor="w", pady=(3, 0))
            e = tk.Entry(right_frame)
            e.pack(fill=tk.X, ipady=2)
            self.entries[key] = e

        tk.Label(right_frame, text="System Root (修改将同步同级文件):", bg="#ecf0f1", font=("", 9, "bold"),
                 fg="#e67e22").pack(anchor="w", pady=(8, 0))
        self.txt_root = tk.Text(right_frame, height=5, wrap=tk.WORD, font=("Consolas", 9))
        self.txt_root.pack(fill=tk.X)

        tk.Button(right_frame, text="🔥 确认修改并同步", command=self.update_metadata_and_sync, bg="#34495e", fg="white",
                  height=1).pack(fill=tk.X, pady=15)
        tk.Button(right_frame, text="📂 定位文件位置", command=self.open_folder, bg="#bdc3c7", height=1).pack(fill=tk.X,
                                                                                                             pady=2)
        tk.Button(right_frame, text="🚀 启动项目", command=self.launch_pdps, bg="#27ae60", fg="white",
                  font=("", 12, "bold"), height=2).pack(fill=tk.X, side=tk.BOTTOM, pady=10)

        # 修复：添加状态栏
        self.status_label = tk.Label(right_frame, text="就绪", bg="#ecf0f1", anchor="w",
                                     font=("微软雅黑", 8), fg="#7f8c8d")
        self.status_label.pack(fill=tk.X, pady=(10, 0))

    def setup_tree_context_menu(self):
        """设置树形控件的右键菜单"""
        self.tree_menu = tk.Menu(self.root, tearoff=0)
        self.tree_menu.add_command(label="复制完整路径", command=self.copy_full_path)
        self.tree_menu.add_command(label="打开所在文件夹", command=self.open_file_location)
        self.tree_menu.add_separator()
        self.tree_menu.add_command(label="查看文件属性", command=self.show_file_properties)

        # 绑定右键事件
        self.tree.bind("<Button-3>", self.show_tree_context_menu)

    def show_tree_context_menu(self, event):
        """显示右键菜单"""
        item = self.tree.identify_row(event.y)
        if item and item.isdigit():  # 只对文件项显示菜单
            self.tree.selection_set(item)
            self.tree_menu.post(event.x_root, event.y_root)

    def copy_full_path(self):
        """复制完整路径到剪贴板"""
        sel = self.tree.selection()
        if sel and sel[0].isdigit():
            idx = int(sel[0])
            full_path = self.df_data.loc[idx, "Full Path"]  # 修复：从数据源获取
            if full_path:
                self.root.clipboard_clear()
                self.root.clipboard_append(full_path)
                self.status_label.config(text=f"已复制: {full_path}")

    def open_file_location(self):
        """打开文件所在位置"""
        self.open_folder()

    def show_file_properties(self):
        """显示文件属性"""
        sel = self.tree.selection()
        if sel and sel[0].isdigit():
            idx = int(sel[0])
            row = self.df_data.loc[idx]
            full_path = row["Full Path"]

            if os.path.exists(full_path):
                file_size = os.path.getsize(full_path)
                file_time = os.path.getmtime(full_path)
                from datetime import datetime

                info = f"""文件属性:

路径: {full_path}
大小: {file_size / 1024:.2f} KB
修改时间: {datetime.fromtimestamp(file_time).strftime('%Y-%m-%d %H:%M:%S')}
公司: {row['Company']}
厂房: {row['Plant']}
项目: {row['Project Name']}
代号: {row['Project Code']}"""

                messagebox.showinfo("文件属性", info)
            else:
                messagebox.showwarning("警告", "文件不存在")

    # --- 核心逻辑方法 ---
    def auto_extract_info(self, psz_path):
        path_parts = os.path.normpath(psz_path).split(os.sep)
        psz_dir = os.path.dirname(psz_path)
        info = {"Company": "", "Plant": "", "Area": "默认区域", "Project Name": "", "Project Code": "",
                "System Root": ""}

        # 1. 基于 Library 的区域识别
        try:
            subfolders = [f for f in os.listdir(psz_dir) if os.path.isdir(os.path.join(psz_dir, f))]
            if any("library" in f.lower() for f in subfolders):
                info["System Root"] = os.path.normpath(
                    os.path.join(psz_dir, next(f for f in subfolders if "library" in f.lower())))
                info["Area"] = "项目根目录" if "项目" in os.path.basename(psz_dir) else os.path.basename(psz_dir)
            else:
                info["System Root"] = os.path.normpath(os.path.join(psz_dir, ".."))
        except Exception as e:
            pass

        # 2. 项目与公司深度识别
        proj_idx = next((i for i, p in enumerate(path_parts) if "项目" in p), -1)
        comp_idx = next((i for i, p in enumerate(path_parts) if "公司" in p), -1)

        if proj_idx != -1:
            part = path_parts[proj_idx]

            # 使用递归匹配所有括号，找到最外层匹配的括号对
            def find_last_bracket_pair(text):
                stack = []
                bracket_pairs = {'(': ')', '（': '）'}

                # 从后向前遍历
                for i in range(len(text) - 1, -1, -1):
                    char = text[i]
                    if char in [')', '）']:
                        stack.append((char, i))
                    elif char in ['(', '（']:
                        if stack and ((char == '(' and stack[-1][0] == ')') or
                                      (char == '（' and stack[-1][0] == '）')):
                            end_pos = stack[-1][1]
                            return i, end_pos, char
                        else:
                            stack = []
                return None

            bracket_info = find_last_bracket_pair(part)

            if bracket_info:
                start_idx, end_idx, bracket_type = bracket_info
                info["Project Code"] = part[start_idx + 1:end_idx].strip()
                info["Project Name"] = part[:start_idx].strip()
            else:
                info["Project Name"], info["Project Code"] = part, ""

            if proj_idx > 0:
                info["Plant"] = path_parts[proj_idx - 1]

            info["Company"] = path_parts[comp_idx] if comp_idx != -1 else (
                path_parts[proj_idx - 2] if proj_idx > 1 else "")
        elif comp_idx != -1:
            info["Company"] = path_parts[comp_idx]
            info["Project Name"] = "未识别项目"

        if not info["Company"]: info["Company"] = "通用客户"
        if not info["Plant"]: info["Plant"] = "未知厂房"
        if not info["Project Name"]: info["Project Name"] = os.path.basename(psz_path)
        return info

    def refresh_tree(self, target_id=None):
        expanded = []
        for item in self.tree.get_children(''): self._get_expanded(item, expanded)

        # 筛选逻辑
        df = self.df_data.copy()
        if self.filter_company_var.get() != "全部公司":
            df = df[df["Company"] == self.filter_company_var.get()]
        kw = self.search_var.get().strip().lower()
        if kw:
            df = df[df.apply(lambda r: kw in str(r.values).lower(), axis=1)]

        self.combo_company['values'] = ["全部公司"] + sorted(list(self.df_data["Company"].unique()))
        for item in self.tree.get_children(): self.tree.delete(item)

        is_searching = bool(kw)
        from datetime import datetime

        for c, c_grp in df.groupby("Company"):
            company_node = self.tree.insert("", "end", iid=f"c_{c}", text=f"🏢 {c}", open=is_searching)
            for p, p_grp in c_grp.groupby("Plant"):
                plant_node = self.tree.insert(company_node, "end", iid=f"p_{c}_{p}", text=f"🏭 {p}", open=is_searching)
                for pr, pr_grp in p_grp.groupby("Project Name"):
                    project_node = self.tree.insert(plant_node, "end", iid=f"pr_{c}_{p}_{pr}", text=f"📂 {pr}",
                                                    values=(pr_grp.iloc[0]["Project Code"], ""), open=is_searching)
                    for a, a_grp in pr_grp.groupby("Area"):
                        area_node = self.tree.insert(project_node, "end", iid=f"a_{c}_{p}_{pr}_{a}", text=f"📁 {a}",
                                                     open=is_searching)
                        for idx, row in a_grp.iterrows():
                            # 获取文件信息
                            full_path = row['Full Path']
                            file_size = ""
                            modified_time = ""
                            status = "Ready"

                            try:
                                if os.path.exists(full_path):
                                    # 获取文件大小（转换为 KB/MB）
                                    size_bytes = os.path.getsize(full_path)
                                    if size_bytes < 1024:
                                        file_size = f"{size_bytes} B"
                                    elif size_bytes < 1024 * 1024:
                                        file_size = f"{size_bytes / 1024:.1f} KB"
                                    else:
                                        file_size = f"{size_bytes / (1024 * 1024):.1f} MB"

                                    # 获取修改时间
                                    mtime = os.path.getmtime(full_path)
                                    modified_time = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')
                                else:
                                    status = "Missing"
                                    file_size = "N/A"
                                    modified_time = "N/A"
                            except Exception as e:
                                status = "Error"
                                file_size = "N/A"
                                modified_time = "N/A"

                            self.tree.insert(area_node, "end", iid=str(idx), text=f"📄 {row['File Name']}",
                                             values=("", status, full_path, row['File Name'], file_size, modified_time))

        if not is_searching:
            for node_id in expanded:
                if self.tree.exists(node_id): self.tree.item(node_id, open=True)
        if target_id and self.tree.exists(target_id):
            self._expand_to(target_id)
            self.tree.selection_set(target_id)
            self.tree.see(target_id)

    def _get_expanded(self, item, res):
        if self.tree.item(item, 'open'): res.append(item)
        for c in self.tree.get_children(item): self._get_expanded(c, res)

    def _expand_to(self, item):
        parent = self.tree.parent(item)
        if parent:
            self.tree.item(parent, open=True)
            self._expand_to(parent)

    def update_metadata_and_sync(self):
        sel = self.tree.selection()
        if not sel or not sel[0].isdigit():
            return

        idx = int(sel[0])

        # 预计算当前目录
        current_row = self.df_data.iloc[idx]
        curr_dir = os.path.dirname(current_row["Full Path"])
        new_root = self.txt_root.get("1.0", tk.END).strip()

        # 获取新值
        new_values = {k: self.entries[k].get() for k in self.entries}
        new_values["System Root"] = new_root

        # 一次性更新所有需要同步的行
        mask = (
                (self.df_data.index != idx) &
                (self.df_data["Full Path"].apply(lambda x: os.path.dirname(x)) == curr_dir)
        )

        sync_indices = self.df_data[mask].index.tolist()

        # 更新数据
        for col, value in new_values.items():
            self.df_data.at[idx, col] = value
            if col in self.df_data.columns and len(sync_indices) > 0:
                self.df_data.loc[sync_indices, col] = value

        # 刷新显示
        self.refresh_tree(target_id=str(idx))

        if len(sync_indices) > 0:
            messagebox.showinfo("同步成功", f"已更新同级 {len(sync_indices)} 个文件")

    def on_item_select(self, event):
        sel = self.tree.selection()
        if not sel or not sel[0].isdigit(): return
        row = self.df_data.loc[int(sel[0])]
        for k in self.entries:
            self.entries[k].delete(0, tk.END)
            self.entries[k].insert(0, str(row[k]))
        self.txt_root.delete("1.0", tk.END)
        self.txt_root.insert("1.0", str(row["System Root"]))
        self.load_preview(row["Full Path"])

    def open_folder(self):
        sel = self.tree.selection()
        if not sel or not sel[0].isdigit(): return
        import subprocess
        subprocess.run(['explorer', '/select,', os.path.normpath(self.df_data.loc[int(sel[0]), "Full Path"])])

    def open_catia(self):
        """打开 CATIA 软件（从注册表获取安装路径）"""
        import subprocess
        import sys

        def get_catia_path_from_registry():
            """从注册表获取 CATIA 安装路径"""
            catia_path = None

            if sys.platform == 'win32':
                try:
                    # 尝试多个可能的注册表路径
                    reg_paths = [
                        (r"SOFTWARE\Dassault Systemes\B32\0", "DEST_FOLDER_OSDS"),
                        (r"SOFTWARE\Dassault Systemes\B31\0", "DEST_FOLDER_OSDS"),
                        (r"SOFTWARE\Dassault Systemes\B30\0", "DEST_FOLDER_OSDS"),
                        (r"SOFTWARE\Dassault Systemes\B29\0", "DEST_FOLDER_OSDS"),
                        (r"SOFTWARE\Dassault Systemes\B28\0", "DEST_FOLDER_OSDS"),
                        (r"SOFTWARE\Dassault Systemes\B27\0", "DEST_FOLDER_OSDS"),
                        (r"SOFTWARE\Wow6432Node\Dassault Systemes\B32\0", "DEST_FOLDER_OSDS"),
                        (r"SOFTWARE\Wow6432Node\Dassault Systemes\B28\0", "DEST_FOLDER_OSDS"),
                    ]

                    root_keys = [
                        (winreg.HKEY_LOCAL_MACHINE, "HKLM"),
                        (winreg.HKEY_CURRENT_USER, "HKCU"),
                    ]

                    for root_key, root_name in root_keys:
                        for reg_path, value_name in reg_paths:
                            try:
                                with winreg.OpenKey(root_key, reg_path) as key:
                                    install_dir, reg_type = winreg.QueryValueEx(key, value_name)

                                    possible_executables = [
                                        os.path.join(install_dir, r"code\bin\CNEXT.exe"),
                                        os.path.join(install_dir, r"code\bin\CATSTART.exe"),
                                        os.path.join(install_dir, r"code\bin\CATIA.exe"),
                                    ]

                                    for exe_path in possible_executables:
                                        if os.path.exists(exe_path):
                                            return exe_path

                            except WindowsError:
                                continue

                except Exception as e:
                    print(f"读取注册表时出错: {e}")

            return catia_path

        catia_exe = get_catia_path_from_registry()

        if not catia_exe:
            default_paths = [
                r"C:\Program Files\Dassault Systemes\B32\win_b64\code\bin\CNEXT.exe",
                r"C:\Program Files\Dassault Systemes\B28\win_b64\code\bin\CNEXT.exe",
                r"C:\Program Files\Dassault Systemes\B32\win_b64\code\bin\CATSTART.exe",
                r"C:\Program Files\Dassault Systemes\B28\win_b64\code\bin\CATSTART.exe",
            ]

            for path in default_paths:
                if os.path.exists(path):
                    catia_exe = path
                    break

        if not catia_exe:
            catia_exe = filedialog.askopenfilename(
                title="请选择 CATIA 可执行文件",
                initialdir=r"C:\Program Files\Dassault Systemes",
                filetypes=[("Executable files", "*.exe"), ("All files", "*.*")]
            )

            if not catia_exe:
                return

        try:
            if catia_exe.endswith("CATSTART.exe"):
                if "B32" in catia_exe.upper():
                    env = "CATIA_P3.V5-6R2023.B32"
                elif "B28" in catia_exe.upper():
                    env = "CATIA_P3.V5R28.B28"
                else:
                    env = "CATIA_P3"

                cmd = [catia_exe, "-env", env]
            else:
                cmd = [catia_exe]

            subprocess.Popen(cmd, shell=False, cwd=os.path.dirname(catia_exe))

            version_info = "CATIA"
            if "B32" in catia_exe.upper():
                version_info = "CATIA V5-6R2023 (B32)"
            elif "B28" in catia_exe.upper():
                version_info = "CATIA V5R28 (2022)"

            messagebox.showinfo("成功", f"{version_info} 正在启动...")

        except Exception as e:
            messagebox.showerror("启动失败", f"无法启动 CATIA:\n{str(e)}")

    def launch_pdps(self):
        sel = self.tree.selection()
        if not sel or not sel[0].isdigit(): return
        row = self.df_data.loc[int(sel[0])]
        if str(row["System Root"]).strip():
            try:
                key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH)
                winreg.SetValueEx(key, "System Root Path", 0, winreg.REG_SZ, row["System Root"])
                winreg.CloseKey(key)
            except Exception as e:
                print(f"注册表写入失败: {e}")
        if os.path.exists(row["Full Path"]):
            os.startfile(row["Full Path"])
        else:
            messagebox.showerror("错误", "文件不存在")

    def scan_files(self):
        if not self.search_paths: return
        existing = {row["Full Path"]: row.to_dict() for _, row in self.df_data.iterrows()}
        new_list = []
        for bp in self.search_paths:
            for root, _, files in os.walk(bp):
                for f in files:
                    if f.lower().endswith(".psz"):
                        path = os.path.normpath(os.path.join(root, f))
                        if path in existing:
                            new_list.append(existing[path])
                        else:
                            rec = {"File Name": f, "Full Path": path}
                            rec.update(self.auto_extract_info(path))
                            new_list.append(rec)
        self.df_data = pd.DataFrame(new_list).drop_duplicates(subset=["Full Path"])
        self.refresh_tree()

    def load_preview(self, path):
        try:
            with zipfile.ZipFile(path, 'r') as z:
                if "Preview Images/previmg.jpg" in z.namelist():
                    with z.open("Preview Images/previmg.jpg") as f:
                        img = Image.open(f).copy()
                        tw, th = 350, 220
                        rw, rh = img.size
                        ratio = min(tw / rw, th / rh)
                        self.current_preview_image = ImageTk.PhotoImage(
                            img.resize((int(rw * ratio), int(rh * ratio)), Image.Resampling.LANCZOS))
                        self.lbl_image.config(image=self.current_preview_image, text="")
                        return
        except Exception as e:
            print(f"预览图加载失败: {e}")
        self.lbl_image.config(image='', text="无预览图", fg="white")

    def add_search_folder(self):
        d = filedialog.askdirectory()
        if d:
            self.search_paths.append(d)
            self.scan_files()

    def save_config(self):
        self.df_data.to_excel(CONFIG_FILE, index=False)
        with open("paths_config.ini", "w") as f: f.write(",".join(self.search_paths))
        messagebox.showinfo("保存", "本地数据库已更新")

    def load_local_config(self):
        if os.path.exists(CONFIG_FILE):
            self.df_data = pd.read_excel(CONFIG_FILE).fillna("")
            self.refresh_tree()
        if os.path.exists("paths_config.ini"):
            with open("paths_config.ini", "r") as f:
                self.search_paths = f.read().split(",")


# ==========================================
# 页面模块 2：CATIA 工具箱（核心修复版）
# ==========================================
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import pandas as pd

# 尝试导入 pycatia
try:
    from pycatia import catia
    from pycatia.in_interfaces.application import Application
    from pycatia.product_structure_interfaces.product import Product
    from pycatia.mec_mod_interfaces.part_document import PartDocument
    from pycatia.mec_mod_interfaces.part import Part
    from pycatia.knowledge_interfaces.parameter import Parameter

    PYCATIA_AVAILABLE = True
except ImportError:
    PYCATIA_AVAILABLE = False
    print("⚠️ pycatia 未安装，请运行: pip install pycatia")


# ==================== 核心业务逻辑类（使用 pycatia） ====================

import traceback
from typing import List, Dict, Tuple, Optional

# -*- coding: utf-8 -*-
import threading
import queue
import traceback
from typing import List, Dict, Optional, Tuple
import tkinter as tk
from tkinter import messagebox, ttk, simpledialog
import pandas as pd

# 如果你在模块顶部有 PYCATIA_AVAILABLE 标志，请保留
try:
    from pycatia import catia as pycatia_catia
    PYCATIA_AVAILABLE = True
except Exception:
    PYCATIA_AVAILABLE = False

# ---------------------------
# CATIAWeldPointExtractor
# ---------------------------
import threading
import queue
import traceback
from typing import List, Dict, Optional, Tuple
import tkinter as tk
from tkinter import messagebox, ttk, simpledialog, filedialog
import pandas as pd

# pycatia 可选支持检测
try:
    from pycatia import catia as pycatia_catia
    PYCATIA_AVAILABLE = True
except Exception:
    PYCATIA_AVAILABLE = False

# ---------------------------
# CATIAWeldPointExtractor
# ---------------------------
class CATIAWeldPointExtractor:
    """CATIA 焊点提取工具类（兼容 win32com 和 pycatia）"""

    def __init__(self, catia_app=None):
        self.catia_app = catia_app
        self.weld_points_data: List[Dict] = []
        self.status_message = "就绪"
        self.connection_type = None  # 'win32com' 或 'pycatia'

    def connect_catia(self, use_pycatia=False) -> bool:
        """连接 CATIA 应用（可选 pycatia 或 win32com）"""
        try:
            if use_pycatia:
                if not PYCATIA_AVAILABLE:
                    print("pycatia 未安装")
                    return False
                self.catia_app = pycatia_catia()
                self.connection_type = 'pycatia'
                self.status_message = "已连接 | CATIA (pycatia)"
                return True
            else:
                import win32com.client
                try:
                    self.catia_app = win32com.client.GetActiveObject("CATIA.Application")
                except Exception:
                    self.catia_app = win32com.client.Dispatch("CATIA.Application")
                self.connection_type = 'win32com'
                self.status_message = "已连接 | CATIA (win32com)"
                return True
        except Exception as e:
            self.log_error("连接 CATIA", e)
            self.status_message = "连接 CATIA 失败"
            return False

    def set_catia_app(self, catia_app, connection_type='win32com'):
        """设置 CATIA 应用实例和连接类型"""
        self.catia_app = catia_app
        self.connection_type = connection_type
        self.status_message = f"CATIA 应用已设置 ({connection_type})"

    def _generate_standard_name(self, original_name: str, part_name: str) -> str:
        """
        根据标准生成新名称: {前缀}_{零件中段}_{焊点中段}
        零件: DPUB-501037085-XWS_00 -> 501037085
        焊点: EHX-0000028059-RSW-GSMPoint.66 -> 前缀: EHX, 数字: 28059
        """
        try:
            # 1. 从原始名称中提取前缀（第一个'-'之前的部分）
            # 例如：EHX-0000028059-RSW-GSMPoint.66 -> 前缀: EHX
            original_segments = original_name.split('-')
            if len(original_segments) > 0:
                prefix = original_segments[0]  # 提取前缀
            else:
                prefix = "UnknownPrefix"
                print(f"警告: 无法从原始名称 '{original_name}' 中提取前缀")

            # 2. 提取零件名称中的数字部分
            part_segments = part_name.split('-')
            if len(part_segments) > 1:
                part_id = part_segments[1]  # 假设格式为 XXX-数字-XXX
            else:
                part_id = "UnknownPart"
                print(f"警告: 无法从零件名称 '{part_name}' 中提取零件ID")

            # 3. 提取焊点名称中的数字部分（去掉前导零）
            if len(original_segments) > 1:
                weld_id_raw = original_segments[1]
                # 去掉前导零，但如果全部是零则保留一个零
                weld_id = weld_id_raw.lstrip('0')
                if not weld_id:  # 如果去掉前导零后为空字符串
                    weld_id = "0"
            else:
                weld_id = "UnknownWeld"
                print(f"警告: 无法从原始名称 '{original_name}' 中提取焊点ID")

            # 返回格式: {前缀}_{零件数字部分}_{焊点数字部分}
            return f"{prefix}_{part_id}_{weld_id}"

        except Exception as e:
            print(f"重命名失败 ({original_name}): {e}")
            return original_name  # 失败则返回原名

    def extract_weld_points(self, auto_rename: bool = False) -> List[Dict]:
        """提取焊点数据（兼容两种连接方式）"""
        if not self.catia_app:
            self.status_message = "请先连接 CATIA"
            return []

        self.weld_points_data = []  # 清空之前的数据

        try:
            # 根据连接类型获取活动文档
            if self.connection_type == 'pycatia':
                doc = self.catia_app.active_document
                if not doc:
                    self.status_message = "没有活动的 CATIA 文档"
                    return []
                if not hasattr(doc, 'part'):
                    self.status_message = "当前文档不是 Part 文档 (.CATPart)"
                    return []
                part = doc.part
                selection = doc.selection
                part_full_name = doc.name
            else:  # win32com
                doc = self.catia_app.ActiveDocument
                if not doc:
                    self.status_message = "没有活动的 CATIA 文档"
                    return []
                is_part = False
                try:
                    part = doc.Part
                    is_part = True
                except:
                    pass
                if not is_part:
                    self.status_message = "当前文档不是 Part 文档 (.CATPart)"
                    return []
                selection = doc.Selection
                part_full_name = doc.Name

            # 准备 SPAWorkbench（可选）
            spa_workbench = None
            if self.connection_type == 'pycatia':
                try:
                    spa_workbench = doc.spa_workbench()
                except:
                    print("⚠️ 无法加载 SPAWorkbench (pycatia)")
            else:
                try:
                    spa_workbench = doc.GetWorkbench("SPAWorkbench")
                except:
                    print("⚠️ 无法加载 SPAWorkbench (win32com)")

            # 搜索所有点
            if self.connection_type == 'pycatia':
                selection.clear()
                selection.search("CATGmoSearch.Point,all")
                count = selection.count
            else:
                selection.Clear()
                selection.Search("CATGmoSearch.Point,all")
                count = selection.Count

            successful_count = 0
            failed_count = 0

            for i in range(1, count + 1):
                try:
                    if self.connection_type == 'pycatia':
                        item = selection.item(i)
                        point_obj = item.value
                        name = getattr(point_obj, 'name', f"Point_{i}")
                    else:
                        item = selection.Item(i)
                        point_obj = item.Value
                        name = getattr(point_obj, 'Name', f"Point_{i}")

                    coords = self._get_point_coordinates(point_obj, part, spa_workbench)

                    if coords:
                        # --- 执行重命名逻辑 ---
                        display_name = name
                        if auto_rename:
                            display_name = self._generate_standard_name(name, part_full_name)

                        x, y, z = coords
                        p_type = "焊点" if ("Weld" in name or "Spot" in name) else "普通点"
                        class_val = "PmWeldPoint"
                        ext_id = name
                        location_str = f"{x:.3f},{y:.3f},{z:.3f}"
                        x_str = f"{x:.3f}"
                        y_str = f"{y:.3f}"
                        z_str = f"{z:.3f}"
                        type_str = p_type
                        self.weld_points_data.append({
                            "Class": class_val,
                            "ExternalId": ext_id,
                            "Name": display_name,
                            "Location": location_str,
                            "X": x_str,
                            "Y": y_str,
                            "Z": z_str,
                            "Type": type_str
                        })
                        successful_count += 1
                    else:
                        print(f"❌ 无法提取点 '{name}' 的坐标")
                        failed_count += 1

                except Exception as e:
                    print(f"处理点 {i} 失败: {e}")
                    failed_count += 1
                    continue

            msg = f"提取完成: 成功 {successful_count} 个"
            if failed_count > 0:
                msg += f"，失败 {failed_count} 个"
            self.status_message = msg

            # 清理选择
            if self.connection_type == 'pycatia':
                selection.clear()
            else:
                selection.Clear()

        except Exception as e:
            self.log_error("提取焊点", e)
            self.status_message = f"发生意外错误: {str(e)}"

        return self.weld_points_data

    def _get_point_coordinates(self, point_obj, part, spa_workbench=None) -> Optional[Tuple[float, float, float]]:
        """获取坐标的核心逻辑（兼容两种连接方式）"""
        # 尝试 SPAWorkbench
        if spa_workbench and part:
            try:
                if self.connection_type == 'pycatia':
                    reference = part.create_reference_from_object(point_obj)
                    measurable = spa_workbench.get_measurable(reference)
                    coords = measurable.get_point()
                else:
                    reference = part.CreateReferenceFromObject(point_obj)
                    measurable = spa_workbench.GetMeasurable(reference)
                    coords = measurable.GetPoint()
                if coords and len(coords) == 3:
                    return coords[0], coords[1], coords[2]
            except:
                pass

        # 尝试直接属性
        try:
            if hasattr(point_obj, 'X') and hasattr(point_obj, 'Y') and hasattr(point_obj, 'Z'):
                if self.connection_type == 'pycatia':
                    val_x = point_obj.x if hasattr(point_obj, 'x') else point_obj.X
                    val_y = point_obj.y if hasattr(point_obj, 'y') else point_obj.Y
                    val_z = point_obj.z if hasattr(point_obj, 'z') else point_obj.Z
                else:
                    val_x = point_obj.X.Value if hasattr(point_obj.X, 'Value') else float(point_obj.X)
                    val_y = point_obj.Y.Value if hasattr(point_obj.Y, 'Value') else float(point_obj.Y)
                    val_z = point_obj.Z.Value if hasattr(point_obj.Z, 'Value') else float(point_obj.Z)
                return float(val_x), float(val_y), float(val_z)
        except:
            pass

        # 尝试 GetCoordinates
        try:
            if self.connection_type == 'pycatia':
                if hasattr(point_obj, 'get_coordinates'):
                    coords = point_obj.get_coordinates()
                    if coords and len(coords) == 3:
                        return coords[0], coords[1], coords[2]
            else:
                coords_ret = point_obj.GetCoordinates()
                if coords_ret and len(coords_ret) == 3:
                    return coords_ret[0], coords_ret[1], coords_ret[2]
                coords_buf = [0.0, 0.0, 0.0]
                point_obj.GetCoordinates(coords_buf)
                if coords_buf != [0.0, 0.0, 0.0]:
                    return coords_buf[0], coords_buf[1], coords_buf[2]
        except:
            pass

        return None

    def get_data(self) -> List[Dict]:
        """获取已提取的焊点数据"""
        return self.weld_points_data

    def get_status(self) -> str:
        """获取当前状态信息"""
        return self.status_message

    def clear_data(self):
        """清空已提取的数据"""
        self.weld_points_data = []
        self.status_message = "数据已清空"

    def get_document_names(self) -> Dict[str, str]:
        """
        获取当前活动文档的名称信息

        Returns:
            Dict: 包含以下键的字典：
                - 'document_name': 文档名称（包括后缀）
                - 'document_name_no_ext': 文档名称（不包括后缀）
                - 'part_name': Part 名称（仅 CATPart 文件）
                - 'product_name': Product 名称（仅 CATProduct 文件）
                - 'document_type': 文档类型（'CATPart' 或 'CATProduct'）
                - 'full_path': 文档完整路径
        """
        self.document_info = {}

        if not self.catia_app:
            self.status_message = "请先连接 CATIA"
            return self.document_info

        try:
            if self.connection_type == 'pycatia':
                doc = self.catia_app.active_document()
                if not doc:
                    self.status_message = "没有活动的 CATIA 文档"
                    return self.document_info

                # 获取文档名称和路径
                doc_name = doc.name
                doc_full_path = doc.full_name
            else:  # win32com
                doc = self.catia_app.ActiveDocument
                if not doc:
                    self.status_message = "没有活动的 CATIA 文档"
                    return self.document_info

                doc_name = doc.Name
                doc_full_path = doc.FullName

            # 提取文件名和扩展名
            import os
            doc_name_no_ext = os.path.splitext(doc_name)[0]
            _, doc_ext = os.path.splitext(doc_name)

            # 判断文档类型并获取相应的名称
            document_type = self._determine_document_type(doc)

            self.document_info = {
                'document_name': doc_name,
                'document_name_no_ext': doc_name_no_ext,
                'part_name': None,
                'product_name': None,
                'document_type': document_type,
                'full_path': doc_full_path
            }

            # 根据文档类型获取名称
            if document_type == 'CATPart':
                part_name = self._get_part_name(doc)
                self.document_info['part_name'] = part_name
            elif document_type == 'CATProduct':
                product_name = self._get_product_name(doc)
                self.document_info['product_name'] = product_name

            self.status_message = f"文档信息已获取: {doc_name}"
            return self.document_info

        except Exception as e:
            self.log_error("获取文档名称", e)
            return self.document_info

    def _determine_document_type(self, doc) -> str:
        """
        判断当前文档的类型

        Returns:
            str: 'CATPart', 'CATProduct' 或 'Unknown'
        """
        try:
            if self.connection_type == 'pycatia':
                # pycatia 中，有 part 属性的是 CATPart，有 product 属性的是 CATProduct
                if hasattr(doc, 'part') and doc.part() is not None:
                    return 'CATPart'
                elif hasattr(doc, 'product') and doc.product() is not None:
                    return 'CATProduct'
            else:  # win32com
                # win32com 中，有 Part 属性的是 CATPart，有 Product 属性的是 CATProduct
                try:
                    _ = doc.Part
                    return 'CATPart'
                except:
                    pass
                try:
                    _ = doc.Product
                    return 'CATProduct'
                except:
                    pass
        except Exception as e:
            print(f"判断文档类型失败: {e}")

        return 'Unknown'

    def _get_part_name(self, doc) -> Optional[str]:
        """
        获取 CATPart 文档的零件名称

        Args:
            doc: CATIA 文档对象

        Returns:
            str: 零件名称，如果获取失败返回 None
        """
        try:
            if self.connection_type == 'pycatia':
                part = doc.part()
                if part:
                    part_name = part.name
                    return part_name
            else:  # win32com
                part = doc.Part
                if part:
                    part_name = part.Name
                    return part_name
        except Exception as e:
            print(f"获取 Part 名称失败: {e}")

        return None

    def _get_product_name(self, doc) -> Optional[str]:
        """
        获取 CATProduct 文档的产品名称

        Args:
            doc: CATIA 文档对象

        Returns:
            str: 产品名称，如果获取失败返回 None
        """
        try:
            if self.connection_type == 'pycatia':
                product = doc.product()
                if product:
                    product_name = product.name
                    return product_name
            else:  # win32com
                product = doc.Product
                if product:
                    product_name = product.Name
                    return product_name
        except Exception as e:
            print(f"获取 Product 名称失败: {e}")

        return None

    def get_part_and_product_names(self) -> Tuple[Optional[str], Optional[str]]:
        """
        同时获取零件名称和产品名称

        Returns:
            Tuple: (part_name, product_name)
                - 如果是 CATPart 文档，返回 (part_name, None)
                - 如果是 CATProduct 文档，返回 (None, product_name)
                - 如果都不是或获取失败，返回 (None, None)
        """
        doc_info = self.get_document_names()

        part_name = doc_info.get('part_name')
        product_name = doc_info.get('product_name')

        return part_name, product_name

    def get_document_info(self) -> Dict[str, str]:
        """
        获取完整的文档信息（包括零件名、产品名等）

        Returns:
            Dict: 包含所有文档信息的字典
        """
        if not self.document_info:
            self.get_document_names()
        return self.document_info

    def catia_getname(self) -> Dict[str, str]:
        """
        兼容原有接口：获取 CATIA 零件和产品信息

        Returns:
            Dict: 文档名称信息字典
        """
        return self.get_document_names()


    # ---------------------------
    # 生成球体（完整实现）
    # ---------------------------
    def generate_spheres_from_points(self, parent_tk=None):
        """
        从焊点生成球体（win32com 版本为主）
        parent_tk: 可选，传入 ToolBoxPage 或主窗口用于更新 status_label 和 after 调度
        """
        # 入口判断（修复原代码错误）
        weld_points_data = self.get_data() if hasattr(self, "get_data") else self.weld_points_data
        if not weld_points_data:
            messagebox.showwarning("警告", "没有焊点数据可生成球体")
            return

        # 参数对话（可替换为更复杂的对话窗口）
        try:
            radius_str = simpledialog.askstring("球体半径", "请输入球体半径（mm）:", initialvalue="10", parent=parent_tk)
            if radius_str is None:
                return
            radius = float(radius_str)
        except Exception:
            messagebox.showerror("参数错误", "请输入有效的半径数值")
            return

        name_prefix = simpledialog.askstring("名称前缀", "球体名称前缀:", initialvalue="SPHERE", parent=parent_tk) or "SPHERE"
        geom_set_name = simpledialog.askstring("几何集名称", "几何集名称:", initialvalue="Geometry_Spheres", parent=parent_tk) or "Geometry_Spheres"

        # 创建进度对话
        progress_dialog = tk.Toplevel(parent_tk) if parent_tk else tk.Toplevel()
        progress_dialog.title("生成球体")
        progress_dialog.geometry("420x160")
        progress_dialog.transient(parent_tk)
        progress_dialog.grab_set()

        tk.Label(progress_dialog, text=f"将生成 {len(weld_points_data)} 个球体", font=("微软雅黑", 10)).pack(pady=(10, 5))
        progress_var = tk.DoubleVar(value=0.0)
        progress_bar = ttk.Progressbar(progress_dialog, maximum=100.0, variable=progress_var)
        progress_bar.pack(fill=tk.X, padx=20, pady=(5, 10))

        status_lbl = tk.Label(progress_dialog, text="准备中...", fg="gray")
        status_lbl.pack()

        cancel_event = threading.Event()
        progress_q = queue.Queue()

        def worker():
            """在后台线程中执行 CATIA 操作，注意在此线程内初始化 COM"""
            try:
                import pythoncom
                pythoncom.CoInitialize()
                try:
                    import win32com.client
                    try:
                        catia = win32com.client.GetActiveObject("CATIA.Application")
                    except Exception:
                        catia = win32com.client.Dispatch("CATIA.Application")

                    # 获取文档与 Part
                    doc = getattr(catia, "ActiveDocument", None)
                    if doc is None:
                        progress_q.put(("error", "没有活动的 CATIA 文档"))
                        return

                    part = getattr(doc, "Part", None)
                    if part is None:
                        progress_q.put(("error", "当前文档不是 Part 类型"))
                        return

                    hsf = part.HybridShapeFactory
                    hybrid_bodies = part.HybridBodies
                    origin_elements = part.OriginElements

                    # 尝试切换工作台（可选）
                    try:
                        catia.StartWorkbench("PartDesign")
                    except:
                        pass

                    # 获取或创建几何集
                    target_body = None
                    try:
                        for i in range(1, hybrid_bodies.Count + 1):
                            body = hybrid_bodies.Item(i)
                            if body.Name == geom_set_name:
                                target_body = body
                                break
                        if target_body is None:
                            target_body = hybrid_bodies.Add()
                            target_body.Name = geom_set_name
                    except Exception as e:
                        progress_q.put(("error", f"无法获取或创建几何集: {str(e)}"))
                        return

                    # 尝试设置 InWorkObject
                    try:
                        part.InWorkObject = target_body
                    except:
                        pass

                    total = len(weld_points_data)
                    created_points = []
                    # 1) 批量创建点并 append 到几何集（不频繁 Update）
                    for idx, weld in enumerate(weld_points_data):
                        if cancel_event.is_set():
                            progress_q.put(("info", f"已取消，已创建 {len(created_points)} 个点"))
                            return
                        try:
                            x = float(weld.get("X", 0))
                            y = float(weld.get("Y", 0))
                            z = float(weld.get("Z", 0))
                        except Exception:
                            progress_q.put(("warn", f"第 {idx+1} 个坐标解析失败，跳过"))
                            continue
                        try:
                            pt = hsf.AddNewPointCoord(x, y, z)
                            if pt is None:
                                progress_q.put(("warn", f"第 {idx+1} 个点创建失败"))
                                continue
                            target_body.AppendHybridShape(pt)
                            created_points.append(pt)
                        except Exception as e:
                            progress_q.put(("warn", f"第 {idx+1} 个点创建异常: {str(e)}"))
                            continue
                        # 每创建 10 个点更新一次进度
                        if (idx + 1) % 10 == 0 or (idx + 1) == total:
                            progress_q.put(("progress", (idx + 1, total)))

                    # 一次性 Update 以生成引用
                    try:
                        part.Update()
                    except:
                        pass

                    # 2) 为每个点创建球体
                    success = 0
                    fail = 0
                    created_count = len(created_points)
                    for i, pt in enumerate(created_points):
                        if cancel_event.is_set():
                            progress_q.put(("info", f"已取消，已创建 {success} 个球体"))
                            return
                        try:
                            pt_ref = part.CreateReferenceFromObject(pt)
                            try:
                                sphere = hsf.AddNewSphere(pt_ref, None, radius, -90.0, 90.0, 0.0, 360.0)
                            except Exception:
                                # 退化签名尝试
                                sphere = hsf.AddNewSphere(pt_ref, None, radius)
                            if sphere is None:
                                fail += 1
                                continue
                            try:
                                target_body.AppendHybridShape(sphere)
                            except:
                                pass
                            try:
                                sphere.Name = f"{name_prefix}_{i+1}"
                            except:
                                pass
                            success += 1
                            # 每 10 个刷新一次窗口
                            if i % 10 == 0:
                                try:
                                    doc.Windows.Item(1).Refresh()
                                except:
                                    pass
                            progress_q.put(("progress", (created_count + i + 1, created_count * 2)))
                        except Exception:
                            fail += 1
                            continue

                    # 最终 Update
                    try:
                        part.Update()
                        doc.Windows.Item(1).Refresh()
                    except:
                        pass

                    progress_q.put(("done", (success, fail)))

                finally:
                    pythoncom.CoUninitialize()
            except Exception:
                progress_q.put(("error", traceback.format_exc()))

        # 启动后台线程
        t = threading.Thread(target=worker, daemon=True)
        t.start()

        # 主线程轮询队列并更新 UI
        def poll():
            try:
                while not progress_q.empty():
                    typ, val = progress_q.get_nowait()
                    if typ == "progress":
                        cur, tot = val
                        pct = min(100.0, (cur / max(1, tot)) * 100.0)
                        progress_var.set(pct)
                        status_lbl.config(text=f"处理中: {cur}/{tot}")
                        if parent_tk and hasattr(parent_tk, "status_label"):
                            parent_tk.status_label.config(text=f"生成中: {cur}/{tot}")
                    elif typ == "warn":
                        print("WARN:", val)
                    elif typ == "info":
                        messagebox.showinfo("信息", val)
                        if parent_tk and hasattr(parent_tk, "status_label"):
                            parent_tk.status_label.config(text=val)
                    elif typ == "error":
                        messagebox.showerror("生成球体失败", val)
                        status_lbl.config(text="生成失败")
                        if parent_tk and hasattr(parent_tk, "status_label"):
                            parent_tk.status_label.config(text="生成失败")
                        try:
                            progress_dialog.destroy()
                        except:
                            pass
                    elif typ == "done":
                        success, fail = val
                        messagebox.showinfo("完成", f"已生成 {success} 个球体，失败 {fail} 个")
                        status_lbl.config(text=f"完成: 成功 {success}，失败 {fail}")
                        if parent_tk and hasattr(parent_tk, "status_label"):
                            parent_tk.status_label.config(text=f"完成: 成功 {success}，失败 {fail}")
                        try:
                            progress_dialog.destroy()
                        except:
                            pass
            except Exception:
                pass
            if t.is_alive():
                if parent_tk:
                    parent_tk.after(200, poll)
                else:
                    progress_dialog.after(200, poll)

        # 取消按钮
        def on_cancel():
            if messagebox.askyesno("取消", "确定要取消生成吗？", parent=progress_dialog):
                cancel_event.set()
                status_lbl.config(text="正在取消...")
        btn_frame = tk.Frame(progress_dialog)
        btn_frame.pack(pady=8)
        tk.Button(btn_frame, text="取消", command=on_cancel, bg="#7f8c8d", fg="white", width=12).pack()

        # 启动轮询
        if parent_tk:
            parent_tk.after(200, poll)
        else:
            progress_dialog.after(200, poll)

    def log_error(self, context, error):
        """记录错误信息"""
        error_msg = f"Error in {context}: {error}"
        print(error_msg)
        traceback.print_exc()
        self.status_message = f"错误: {context}"

class CATIASphereScanner:
    """CATIA 几何体重心扫描和点生成工具类（基于 pycatia 示例优化）"""

    def __init__(self, catia_app=None):
        self.catia_app = catia_app
        self.sphere_data = []

    def connect_catia(self) -> bool:
        """连接 CATIA 应用"""
        if not PYCATIA_AVAILABLE:
            raise Exception("pycatia 未安装，请运行: pip install pycatia")
        try:
            self.catia_app = catia()
            return True
        except Exception as e:
            print(f"❌ 连接 CATIA 失败: {e}")
            return False

    def set_catia_app(self, catia_app):
        self.catia_app = catia_app

    def scan_selected_objects(self) -> List[Dict]:
        """扫描当前选中的几何体重心"""
        if not self.catia_app:
            raise Exception("CATIA 未连接")

        try:
            # 1. 获取当前文档并包装为 PartDocument (参考示例逻辑)
            document = self.catia_app.active_document
            part_doc = PartDocument(document.com_object)
            part = part_doc.part
            selection = document.selection

            # 2. 按照示例方式获取 SPA 工作台
            spa_workbench = part_doc.spa_workbench()

            count = selection.count
            if count == 0:
                return []

            self.sphere_data = []
            for i in range(1, count + 1):
                try:
                    selected_obj = selection.item(i).value
                    obj_name = getattr(selected_obj, 'name', f"Object_{i}")

                    # 3. 创建引用并测量 (核心修改点)
                    reference = part.create_reference_from_object(selected_obj)
                    measurable = spa_workbench.get_measurable(reference)

                    # 获取重心坐标 (cog 是一个包含 3 个 float 的元组)
                    cog = measurable.get_cog()

                    # 尝试获取体积以计算半径
                    try:
                        volume = measurable.volume
                    except:
                        volume = 0

                    radius = (volume * 3 / (4 * 3.14159265359)) ** (1 / 3) if volume > 0 else 0

                    self.sphere_data.append({
                        "Name": obj_name,
                        "X": f"{cog[0]:.2f}",
                        "Y": f"{cog[1]:.2f}",
                        "Z": f"{cog[2]:.2f}",
                        "Radius": f"{radius:.2f}",
                        "Volume": round(volume, 3),
                        "Type": "实体" if volume > 0 else "几何图形",
                        "_coords": cog
                    })
                except Exception as e:
                    print(f"⚠️ 无法测量对象 {i}: {e}")

            return self.sphere_data
        except Exception as e:
            traceback.print_exc()
            return []

    def scan_all_bodies(self, search_keywords: List[str] = None) -> List[Dict]:
        """扫描零件中所有的 Bodies"""
        if not self.catia_app:
            raise Exception("CATIA 未连接")

        try:
            document = self.catia_app.active_document
            part_doc = PartDocument(document.com_object)
            part = part_doc.part
            spa_workbench = part_doc.spa_workbench()

            self.sphere_data = []
            bodies = part.bodies

            for i in range(1, bodies.count + 1):
                body = bodies.item(i)

                # 关键词过滤逻辑
                if search_keywords:
                    if not any(kw.lower() in body.name.lower() for kw in search_keywords):
                        continue

                try:
                    # 按照示例：创建 Body 的引用并获取重心
                    reference = part.create_reference_from_object(body)
                    measurable = spa_workbench.get_measurable(reference)
                    cog = measurable.get_cog()
                    volume = measurable.volume

                    self.sphere_data.append({
                        "Name": body.name,
                        "X": f"{cog[0]:.2f}",
                        "Y": f"{cog[1]:.2f}",
                        "Z": f"{cog[2]:.2f}",
                        "Radius": f"{(volume * 3 / (4 * 3.14)) ** (1 / 3):.2f}" if volume > 0 else "0",
                        "Volume": round(volume, 3),
                        "Type": "Body",
                        "_coords": cog
                    })
                except:
                    continue

            return self.sphere_data
        except Exception as e:
            traceback.print_exc()
            return []

    def generate_points_at_centers(self, geometrical_set_name: str = "Geometry_COG_Points") -> int:
        """在计算出的重心位置生成 CATIA 点"""
        if not self.sphere_data:
            return 0

        try:
            part_doc = PartDocument(self.catia_app.active_document.com_object)
            part = part_doc.part
            hs_factory = part.hybrid_shape_factory

            # 获取或创建几何集
            hybrid_bodies = part.hybrid_bodies
            try:
                target_hb = hybrid_bodies.item(geometrical_set_name)
            except:
                target_hb = hybrid_bodies.add()
                target_hb.name = geometrical_set_name

            for data in self.sphere_data:
                x, y, z = data["_coords"]
                new_point = hs_factory.add_new_point_coord(x, y, z)
                new_point.name = f"COG_{data['Name']}"
                target_hb.append_hybrid_shape(new_point)

            part.update()
            return len(self.sphere_data)
        except Exception as e:
            print(f"生成点失败: {e}")
            return 0

    def get_data(self) -> List[Dict]:
        return self.sphere_data


# ==========================================
# 改进版插枪工具 - 可编辑表格 + Excel 粘贴
# ==========================================
"""
新增功能:
1. 表头改为: Name, X, Y, Z, Rx, Ry, Rz (使用欧拉角代替矩阵)
2. 表格支持手动编辑
3. 支持从 Excel 复制粘贴数据
4. 自动将欧拉角转换为旋转矩阵
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import pandas as pd
import numpy as np
from typing import List, Dict, Tuple, Optional
import traceback
import math

# 检测 pycatia
try:
    from pycatia import catia as pycatia_catia

    PYCATIA_AVAILABLE = True
except:
    PYCATIA_AVAILABLE = False


def euler_to_rotation_matrix_kuka(rx: float, ry: float, rz: float) -> np.ndarray:
    """
    库卡机器人欧拉角转旋转矩阵 (ZYX顺序 - RPY)

    库卡标准：A, B, C 分别对应绕 Z, Y, X 轴的旋转
    旋转顺序：先绕 Z 轴旋转 A，再绕 Y 轴旋转 B，最后绕 X 轴旋转 C
    即：R = Rz(A) * Ry(B) * Rx(C)

    Args:
        rz: 绕Z轴旋转角度 A (度) - 对应库卡的A
        ry: 绕Y轴旋转角度 B (度) - 对应库卡的B
        rx: 绕X轴旋转角度 C (度) - 对应库卡的C

    Returns:
        3x3 旋转矩阵
    """
    # 转弧度
    rx_rad = math.radians(rx)  # 对应库卡的C
    ry_rad = math.radians(ry)  # 对应库卡的B
    rz_rad = math.radians(rz)  # 对应库卡的A

    # 绕X轴旋转矩阵
    Rx = np.array([
        [1, 0, 0],
        [0, math.cos(rx_rad), -math.sin(rx_rad)],
        [0, math.sin(rx_rad), math.cos(rx_rad)]
    ])

    # 绕Y轴旋转矩阵
    Ry = np.array([
        [math.cos(ry_rad), 0, math.sin(ry_rad)],
        [0, 1, 0],
        [-math.sin(ry_rad), 0, math.cos(ry_rad)]
    ])

    # 绕Z轴旋转矩阵
    Rz = np.array([
        [math.cos(rz_rad), -math.sin(rz_rad), 0],
        [math.sin(rz_rad), math.cos(rz_rad), 0],
        [0, 0, 1]
    ])

    # 库卡标准：R = Rz(A) * Ry(B) * Rx(C)
    R = Rz @ Ry @ Rx

    return R


def euler_to_rotation_matrix_catia(rx: float, ry: float, rz: float) -> np.ndarray:
    """
    CATIA 欧拉角转旋转矩阵 (XYZ顺序)

    CATIA 通常使用 XYZ 顺序或 ZYX 顺序，这里假设为 XYZ
    旋转顺序：先绕 X 轴旋转，再绕 Y 轴旋转，最后绕 Z 轴旋转
    即：R = Rx(rx) * Ry(ry) * Rz(rz)

    Args:
        rx: 绕X轴旋转角度 (度)
        ry: 绕Y轴旋转角度 (度)
        rz: 绕Z轴旋转角度 (度)

    Returns:
        3x3 旋转矩阵
    """
    # 转弧度
    rx_rad = math.radians(rx)
    ry_rad = math.radians(ry)
    rz_rad = math.radians(rz)

    # 绕X轴旋转矩阵
    Rx = np.array([
        [1, 0, 0],
        [0, math.cos(rx_rad), -math.sin(rx_rad)],
        [0, math.sin(rx_rad), math.cos(rx_rad)]
    ])

    # 绕Y轴旋转矩阵
    Ry = np.array([
        [math.cos(ry_rad), 0, math.sin(ry_rad)],
        [0, 1, 0],
        [-math.sin(ry_rad), 0, math.cos(ry_rad)]
    ])

    # 绕Z轴旋转矩阵
    Rz = np.array([
        [math.cos(rz_rad), -math.sin(rz_rad), 0],
        [math.sin(rz_rad), math.cos(rz_rad), 0],
        [0, 0, 1]
    ])

    # CATIA 通常使用 XYZ 顺序
    R = Rx @ Ry @ Rz

    return R


class EditableTreeview(ttk.Treeview):
    """可编辑的 Treeview"""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)

        self.entry = None
        self.editing_item = None
        self.editing_column = None

        # 绑定事件
        self.bind('<Double-1>', self.on_double_click)
        self.bind('<Button-3>', self.on_right_click)  # 右键粘贴

        # 创建右键菜单
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="📋 粘贴 (Ctrl+V)", command=self.paste_data)
        self.context_menu.add_command(label="➕ 添加行", command=self.add_row)
        self.context_menu.add_command(label="🗑️ 删除行", command=self.delete_row)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="📊 从Excel粘贴", command=self.paste_from_excel)

        # 绑定粘贴快捷键
        self.bind('<Control-v>', lambda e: self.paste_data())

    def on_double_click(self, event):
        """双击编辑单元格"""
        region = self.identify_region(event.x, event.y)
        if region != 'cell':
            return

        # 识别行和列
        item = self.identify_row(event.y)
        column = self.identify_column(event.x)

        if not item or not column:
            return

        # 获取列索引
        column_index = int(column.replace('#', '')) - 1
        if column_index < 0:
            return

        # 获取当前值
        values = self.item(item, 'values')
        if column_index >= len(values):
            return

        current_value = values[column_index]

        # 创建编辑框
        self.show_edit_entry(item, column_index, current_value, event)

    def show_edit_entry(self, item, column_index, current_value, event):
        """显示编辑输入框"""
        # 删除旧的编辑框
        if self.entry:
            self.entry.destroy()

        # 获取单元格位置
        x, y, width, height = self.bbox(item, column=f'#{column_index + 1}')

        # 创建编辑框
        self.entry = tk.Entry(self)
        self.entry.place(x=x, y=y, width=width, height=height)
        self.entry.insert(0, current_value)
        self.entry.select_range(0, tk.END)
        self.entry.focus()

        # 保存编辑状态
        self.editing_item = item
        self.editing_column = column_index

        # 绑定事件
        self.entry.bind('<Return>', self.save_edit)
        self.entry.bind('<Escape>', lambda e: self.cancel_edit())
        self.entry.bind('<FocusOut>', self.save_edit)

    def save_edit(self, event=None):
        """保存编辑"""
        if not self.entry:
            return

        new_value = self.entry.get()
        values = list(self.item(self.editing_item, 'values'))
        values[self.editing_column] = new_value

        self.item(self.editing_item, values=values)
        self.cancel_edit()

    def cancel_edit(self):
        """取消编辑"""
        if self.entry:
            self.entry.destroy()
            self.entry = None
        self.editing_item = None
        self.editing_column = None

    def on_right_click(self, event):
        """右键菜单"""
        # 选中右键点击的行
        item = self.identify_row(event.y)
        if item:
            self.selection_set(item)

        # 显示菜单
        self.context_menu.post(event.x_root, event.y_root)

    def add_row(self):
        """添加新行"""
        # 默认值 (Name, Type, X, Y, Z, Rx, Ry, Rz, Status)
        default_values = ("New_Gun", "PmWeldLocationOperation", "0", "0", "0", "0", "0", "0", "待插入")
        self.insert("", tk.END, values=default_values)

    def delete_row(self):
        """删除选中行"""
        selected = self.selection()
        if selected:
            for item in selected:
                self.delete(item)

    def paste_data(self):
        """粘贴数据 (单行或多行)"""
        try:
            # 从剪贴板获取数据
            clipboard_data = self.clipboard_get()

            # 按行分割
            rows = clipboard_data.strip().split('\n')

            # 获取当前选中的行
            selected = self.selection()
            if selected:
                insert_index = self.index(selected[0])
                # 删除选中的行
                for item in selected:
                    self.delete(item)
            else:
                insert_index = tk.END

            # 插入数据
            for row_data in rows:
                # 按制表符或空格分割
                if '\t' in row_data:
                    values = row_data.split('\t')
                else:
                    values = row_data.split()

                # 处理数据格式 (Name, Type, X, Y, Z, Rx, Ry, Rz)
                if len(values) >= 8:
                    # 完整数据：Name, Type, X, Y, Z, Rx, Ry, Rz
                    final_values = values[:8] + ["待插入"]
                elif len(values) >= 7:
                    # 缺少Type：Name, X, Y, Z, Rx, Ry, Rz
                    # 插入默认Type
                    final_values = [values[0], "PmWeldLocationOperation"] + values[1:7] + ["待插入"]
                else:
                    # 补全到 8 列
                    while len(values) < 7:
                        values.append("0")
                    final_values = [values[0], "PmWeldLocationOperation"] + values[1:7] + ["待插入"]

                self.insert("", insert_index, values=final_values)
                if insert_index != tk.END:
                    insert_index += 1

            messagebox.showinfo("成功", f"已粘贴 {len(rows)} 行数据")

        except tk.TclError:
            messagebox.showwarning("警告", "剪贴板为空")
        except Exception as e:
            messagebox.showerror("错误", f"粘贴失败:\n{str(e)}")

    def paste_from_excel(self):
        """从 Excel 文件粘贴"""
        file_path = filedialog.askopenfilename(
            title="选择 Excel 文件",
            filetypes=[("Excel 文件", "*.xlsx *.xls"), ("所有文件", "*.*")]
        )

        if not file_path:
            return

        try:
            df = pd.read_excel(file_path)

            # 检查必需列
            required_cols = ['Name', 'X', 'Y', 'Z']
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                messagebox.showerror("错误", f"Excel 缺少必需列: {', '.join(missing_cols)}")
                return

            # 清空现有数据
            for item in self.get_children():
                self.delete(item)

            # 插入数据
            count = 0
            for idx, row in df.iterrows():
                if pd.isna(row['Name']):
                    continue

                name = str(row['Name']).strip()
                point_type = str(row.get('Type', 'PmWeldLocationOperation')).strip()
                x = str(row.get('X', 0))
                y = str(row.get('Y', 0))
                z = str(row.get('Z', 0))
                rx = str(row.get('Rx', 0))
                ry = str(row.get('Ry', 0))
                rz = str(row.get('Rz', 0))

                self.insert("", tk.END, values=(name, point_type, x, y, z, rx, ry, rz, "待插入"))
                count += 1

            messagebox.showinfo("成功", f"已从 Excel 导入 {count} 行数据")

        except Exception as e:
            messagebox.showerror("错误", f"导入失败:\n{str(e)}")

    def get_all_data(self) -> List[Dict]:
        """获取所有数据"""
        data = []
        for item in self.get_children():
            values = self.item(item, 'values')
            if len(values) >= 8:
                try:
                    data.append({
                        'Name': str(values[0]),
                        'Type': str(values[1]),
                        'X': float(values[2]),
                        'Y': float(values[3]),
                        'Z': float(values[4]),
                        'Rx': float(values[5]),
                        'Ry': float(values[6]),
                        'Rz': float(values[7]),
                        'Status': str(values[8]) if len(values) > 8 else '待插入'
                    })
                except ValueError:
                    continue
        return data

    def update_status(self, row_index: int, status: str):
        """更新指定行的状态"""
        items = self.get_children()
        if row_index < len(items):
            item = items[row_index]
            values = list(self.item(item, 'values'))
            if len(values) >= 9:
                values[8] = status
            else:
                values.append(status)
            self.item(item, values=values)


class CatiaGunPlacer:
    """CATIA 焊枪插入工具类"""

    def __init__(self, catia_app=None):
        self.catia_app = catia_app
        self.connection_type = None
        self.status_message = "就绪"
        self.gun_template_path = ""
        self.rotation_order = 'kuka'  # 默认使用库卡顺序

    def set_rotation_order(self, order: str):
        """设置旋转顺序"""
        valid_orders = ['kuka', 'catia_zyx', 'catia_xyz']
        if order in valid_orders:
            self.rotation_order = order
            print(f"旋转顺序设置为: {order}")

    def set_catia_app(self, catia_app, connection_type='win32com'):
        """设置 CATIA 应用实例"""
        self.catia_app = catia_app
        self.connection_type = connection_type
        self.status_message = f"CATIA 已连接 ({connection_type})"

    def is_catia_connected(self) -> bool:
        """检查 CATIA 连接状态"""
        if not self.catia_app:
            return False
        try:
            if self.connection_type == 'pycatia':
                _ = self.catia_app.documents.count
            else:
                _ = self.catia_app.Documents.Count
            return True
        except:
            return False

    def has_active_product(self) -> bool:
        """检查是否有活动的 Product 文档"""
        if not self.is_catia_connected():
            return False
        try:
            if self.connection_type == 'pycatia':
                doc = self.catia_app.active_document
                return hasattr(doc, 'product')
            else:
                doc = self.catia_app.ActiveDocument
                try:
                    _ = doc.Product
                    return True
                except:
                    return False
        except:
            return False

    def euler_to_rotation_matrix(self, rx: float, ry: float, rz: float, order: str = None) -> np.ndarray:
        """
        根据指定的旋转顺序将欧拉角转换为旋转矩阵

        Args:
            rx, ry, rz: 绕X/Y/Z轴的旋转角度(度)
            order: 旋转顺序 ('kuka', 'catia_zyx', 'catia_xyz')

        Returns:
            3x3 旋转矩阵
        """
        if order is None:
            order = self.rotation_order

        # 转弧度
        rx_rad = math.radians(rx)
        ry_rad = math.radians(ry)
        rz_rad = math.radians(rz)

        # 绕X轴旋转矩阵
        Rx = np.array([
            [1, 0, 0],
            [0, math.cos(rx_rad), -math.sin(rx_rad)],
            [0, math.sin(rx_rad), math.cos(rx_rad)]
        ])

        # 绕Y轴旋转矩阵
        Ry = np.array([
            [math.cos(ry_rad), 0, math.sin(ry_rad)],
            [0, 1, 0],
            [-math.sin(ry_rad), 0, math.cos(ry_rad)]
        ])

        # 绕Z轴旋转矩阵
        Rz = np.array([
            [math.cos(rz_rad), -math.sin(rz_rad), 0],
            [math.sin(rz_rad), math.cos(rz_rad), 0],
            [0, 0, 1]
        ])

        # 根据旋转顺序组合矩阵
        if order == 'kuka':
            # 库卡标准：R = Rz(A) * Ry(B) * Rx(C)
            # 注意：库卡的A,B,C对应绕Z,Y,X轴
            # 所以：R = Rz(rz) * Ry(ry) * Rx(rx)
            R = Rz @ Ry @ Rx
        elif order == 'catia_zyx':
            # CATIA ZYX顺序：R = Rz(rz) * Ry(ry) * Rx(rx)
            R = Rz @ Ry @ Rx
        elif order == 'catia_xyz':
            # CATIA XYZ顺序：R = Rx(rx) * Ry(ry) * Rz(rz)
            R = Rx @ Ry @ Rz
        else:
            raise ValueError(f"不支持的旋转顺序: {order}")

        return R

    def kuka_to_catia_rotation(self, a: float, b: float, c: float, target_order: str = 'catia_xyz') -> np.ndarray:
        """
        将库卡机器人欧拉角转换为CATIA旋转矩阵

        库卡机器人：A, B, C 对应绕 Z, Y, X 轴旋转 (ZYX顺序)

        Args:
            a: 绕Z轴旋转角度 (库卡A)
            b: 绕Y轴旋转角度 (库卡B)
            c: 绕X轴旋转角度 (库卡C)
            target_order: 目标旋转顺序

        Returns:
            3x3 旋转矩阵
        """
        # 库卡使用ZYX顺序
        R_kuka = self.euler_to_rotation_matrix(c, b, a, order='kuka')

        # 如果需要转换为其他顺序
        if target_order == 'kuka':
            return R_kuka

        # 注意：这里需要谨慎处理，因为不同顺序的欧拉角不能简单转换
        # 更好的方法是直接使用旋转矩阵

        return R_kuka

    def euler_to_catia_components(self, x: float, y: float, z: float,
                                  rx: float, ry: float, rz: float) -> List[float]:
        """
        将位置和欧拉角转换为 CATIA Position 的 12 参数

        Args:
            x, y, z: 位置坐标
            rx, ry, rz: 欧拉角 (度)

        Returns:
            12 个参数列表 [M00,M10,M20, M01,M11,M21, M02,M12,M22, X,Y,Z]
        """
        # 使用当前设置的旋转顺序
        R = self.euler_to_rotation_matrix(rx, ry, rz)

        # CATIA 需要的列优先顺序
        components = [
            R[0, 0], R[1, 0], R[2, 0],  # 第一列
            R[0, 1], R[1, 1], R[2, 1],  # 第二列
            R[0, 2], R[1, 2], R[2, 2],  # 第三列
            x, y, z  # 平移向量
        ]

        return components

    def place_guns_batch(self, gun_template: str, gun_data: List[Dict],
                         progress_callback=None, correction_values=None,
                         rotation_order=None) -> Tuple[int, int, List[str]]:
        """
        批量插入焊枪（支持多种格式）

        支持的文件格式:
        - .CATPart
        - .CATProduct
        - .cgr

        逻辑:
        1. 创建名为 "产品名-GUN-日期" 的子产品
        2. 在子产品中插入指定格式的文件
        3. 设置位置和姿态

        Args:
            gun_template: 焊枪模板文件路径
            gun_data: 焊枪数据列表
            progress_callback: 进度回调函数

        Returns:
            (成功数量, 失败数量, 状态列表)
        """
        if not self.is_catia_connected():
            messagebox.showerror("错误", "CATIA 未连接")
            return 0, 0, []

        if not self.has_active_product():
            messagebox.showerror("错误", "请先打开一个 Product 文档")
            return 0, 0, []

        # 设置旋转顺序
        if rotation_order:
            self.set_rotation_order(rotation_order)

        # === 路径处理 ===
        gun_template_normalized = os.path.normpath(gun_template)
        gun_template_normalized = gun_template_normalized.replace('/', '\\')

        print(f"原始路径: {gun_template}")
        print(f"规范化路径: {gun_template_normalized}")

        if not os.path.exists(gun_template_normalized):
            messagebox.showerror("错误", f"焊枪模板文件不存在:\n\n{gun_template_normalized}")
            return 0, 0, []

        # === 检测文件类型 ===
        file_ext = os.path.splitext(gun_template_normalized)[1].lower()
        supported_formats = ['.catpart', '.catproduct', '.cgr']

        if file_ext not in supported_formats:
            messagebox.showerror("错误",
                                 f"不支持的文件格式: {file_ext}\n\n"
                                 f"支持的格式: .CATPart, .CATProduct, .cgr")
            return 0, 0, []

        print(f"文件格式: {file_ext}")

        if not gun_data:
            messagebox.showwarning("警告", "没有插枪数据")
            return 0, 0, []

        success_count = 0
        failed_count = 0
        status_list = []
        total = len(gun_data)

        try:
            # 获取当前 Product
            if self.connection_type == 'pycatia':
                doc = self.catia_app.active_document
                root_product = doc.product
                root_products = root_product.products
            else:
                doc = self.catia_app.ActiveDocument
                root_product = doc.Product
                root_products = root_product.Products

            # === 新增：生成唯一容器产品名称（带同名检测）===
            from datetime import datetime
            current_date = datetime.now().strftime("%Y%m%d")
            root_product_name = root_product.Name if hasattr(root_product, 'Name') else root_product.name

            # 基础容器名称
            base_container_name = f"{root_product_name}-GUN-{current_date}"
            container_name = base_container_name

            print(f"\n{'=' * 60}")
            print(f"开始生成容器产品名称...")
            print(f"基础名称: {base_container_name}")

            # 获取所有现有的子产品名称
            existing_names = []
            if self.connection_type == 'pycatia':
                # pycatia 方式：遍历子产品
                for product in root_products:
                    product_name = product.name
                    existing_names.append(product_name)
                    print(f"  现有子产品: {product_name}")
            else:
                # win32com 方式：遍历子产品
                count = root_products.Count
                for i in range(1, count + 1):
                    product_name = root_products.Item(i).Name
                    existing_names.append(product_name)
                    print(f"  现有子产品: {product_name}")

            # 检查是否存在同名，如果存在则添加编号
            counter = 1
            while container_name in existing_names:
                container_name = f"{base_container_name}.{counter:02d}"
                counter += 1

            if container_name != base_container_name:
                print(f"⚠ 发现同名产品，使用新名称: {container_name}")
            else:
                print(f"✓ 容器名称可用: {container_name}")

            print(f"{'=' * 60}")
            print(f"批量插入 {total} 个焊枪组件")
            print(f"最终容器产品名: {container_name}")
            print(f"模板文件: {gun_template_normalized}")
            print(f"文件类型: {file_ext}")
            print(f"{'=' * 60}\n")

            # === 创建容器产品 ===
            print(f"创建容器产品: {container_name}...")

            if self.connection_type == 'pycatia':
                com_root_products = root_products.com_object
                container_product = com_root_products.AddNewComponent("Product", "")
                container_product.Name = container_name
                print(f"  ✓ 容器产品已创建")
                container_products = container_product.Products
            else:
                container_product = root_products.AddNewComponent("Product", "")
                container_product.Name = container_name
                print(f"  ✓ 容器产品已创建")
                container_products = container_product.Products

            # === 批量插入到容器产品中 ===
            for idx, gun in enumerate(gun_data):
                try:
                    if progress_callback:
                        progress_callback(idx + 1, total, f"插入: {gun['Name']}")

                    print(f"\n[{idx + 1}/{total}] 处理 {gun['Name']}...")

                    # === 新增：应用坐标校正 ===
                    if correction_values:
                        # 应用位置校正
                        x_corrected = gun['X'] + correction_values.get('X', 0)
                        y_corrected = gun['Y'] + correction_values.get('Y', 0)
                        z_corrected = gun['Z'] + correction_values.get('Z', 0)

                        # 应用旋转校正
                        rx_corrected = gun['Rx'] + correction_values.get('Rx', 0)
                        ry_corrected = gun['Ry'] + correction_values.get('Ry', 0)
                        rz_corrected = gun['Rz'] + correction_values.get('Rz', 0)

                        print(
                            f"  原始: X={gun['X']}, Y={gun['Y']}, Z={gun['Z']}, Rx={gun['Rx']}, Ry={gun['Ry']}, Rz={gun['Rz']}")
                        print(
                            f"  校正: X={x_corrected}, Y={y_corrected}, Z={z_corrected}, Rx={rx_corrected}, Ry={ry_corrected}, Rz={rz_corrected}")
                    else:
                        x_corrected = gun['X']
                        y_corrected = gun['Y']
                        z_corrected = gun['Z']
                        rx_corrected = gun['Rx']
                        ry_corrected = gun['Ry']
                        rz_corrected = gun['Rz']

                    # 转换欧拉角为变换矩阵（使用校正后的值）
                    components = self.euler_to_catia_components(
                        x_corrected, y_corrected, z_corrected,
                        rx_corrected, ry_corrected, rz_corrected
                    )

                    # === 插入文件 ===
                    count_before = container_products.Count
                    print(f"  容器内产品数: {count_before}")

                    # 只插入选定的模板文件
                    file_array = (gun_template_normalized,)
                    print(f"  插入: {os.path.basename(gun_template_normalized)}")

                    # 添加组件
                    container_products.AddComponentsFromFiles(file_array, "All")
                    print(f"  ✓ AddComponentsFromFiles 调用成功")

                    # 等待 CATIA 处理
                    import time
                    time.sleep(0.2)

                    # 检查是否成功添加
                    count_after = container_products.Count
                    print(f"  容器内产品数: {count_after}")

                    if count_after <= count_before:
                        raise Exception(f"组件未添加成功 (添加前: {count_before}, 添加后: {count_after})")

                    # 获取刚添加的产品（最后一个）
                    p_count = count_after
                    new_product = container_products.Item(p_count)

                    # 重命名
                    old_name = new_product.Name
                    new_product.Name = gun['Name']
                    print(f"  重命名: {old_name} -> {gun['Name']}")

                    # 设置位置和姿态
                    position = new_product.Position
                    position.SetComponents(components)
                    print(f"  ✓ 已设置变换矩阵")

                    print(f"  ✓ 成功插入 {gun['Name']}")

                    status_list.append('✓ 成功')
                    success_count += 1

                except Exception as e:
                    error_msg = f'✗ 失败: {str(e)[:50]}'
                    status_list.append(error_msg)
                    failed_count += 1
                    print(f"  ❌ 插入失败: {e}")
                    traceback.print_exc()

            print(f"\n{'=' * 60}")
            print(f"批量插入完成")
            print(f"容器产品: {container_name}")
            print(f"成功: {success_count} | 失败: {failed_count}")
            print(f"{'=' * 60}\n")

            self.status_message = f"完成 | 成功: {success_count} | 失败: {failed_count}"
            return success_count, failed_count, status_list

        except Exception as e:
            messagebox.showerror("批量插入失败", f"发生错误:\n{str(e)}")
            print(traceback.format_exc())
            return success_count, failed_count, status_list


class CatiaGunPlacementPage(tk.Frame):
    """插枪工具页面 - 改进版"""

    def __init__(self, parent):
        super().__init__(parent)
        self.gun_placer = CatiaGunPlacer()
        self.catia_app = None

        self.setup_ui()
        self.try_auto_connect_catia()

    def setup_ui(self):
        """构建界面"""
        # 顶部控制栏
        control_frame = tk.Frame(self, bg="#2c3e50", pady=10)
        control_frame.pack(fill=tk.X, padx=5, pady=5)

        tk.Label(control_frame, text="🔧 CATIA 焊枪批量插入工具",
                 font=("微软雅黑", 14, "bold"), fg="white", bg="#2c3e50").pack(pady=5)

        # === 新增：坐标校正输入框 ===
        correction_frame = tk.Frame(control_frame, bg="#2c3e50")
        correction_frame.pack(pady=5)

        rotation_frame = tk.Frame(control_frame, bg="#2c3e50")
        rotation_frame.pack(pady=5)

        tk.Label(correction_frame, text="坐标校正:", font=("微软雅黑", 9),
                 fg="white", bg="#2c3e50").pack(side=tk.LEFT, padx=(0, 5))
        tk.Label(rotation_frame, text="旋转顺序:", font=("微软雅黑", 9),
                fg="white", bg="#2c3e50").pack(side=tk.LEFT, padx=(0, 5))

        self.rotation_order_var = tk.StringVar(value="kuka")
        # 库卡顺序（ZYX - 先Z后Y后X）
        rb_kuka = tk.Radiobutton(rotation_frame, text="库卡 (ZYX)",
                                 variable=self.rotation_order_var, value="kuka",
                                 bg="#2c3e50", fg="white", selectcolor="#2c3e50",
                                 command=self.on_rotation_order_changed)
        rb_kuka.pack(side=tk.LEFT, padx=5)

        # CATIA ZYX顺序
        rb_catia_zyx = tk.Radiobutton(rotation_frame, text="CATIA ZYX",
                                      variable=self.rotation_order_var, value="catia_zyx",
                                      bg="#2c3e50", fg="white", selectcolor="#2c3e50",
                                      command=self.on_rotation_order_changed)
        rb_catia_zyx.pack(side=tk.LEFT, padx=5)

        # CATIA XYZ顺序
        rb_catia_xyz = tk.Radiobutton(rotation_frame, text="CATIA XYZ",
                                      variable=self.rotation_order_var, value="catia_xyz",
                                      bg="#2c3e50", fg="white", selectcolor="#2c3e50",
                                      command=self.on_rotation_order_changed)
        rb_catia_xyz.pack(side=tk.LEFT, padx=5)

        # 旋转顺序提示标签
        self.lbl_rotation_info = tk.Label(rotation_frame, text="当前: 库卡 (ZYX)",
                                          font=("微软雅黑", 9), fg="#f1c40f", bg="#2c3e50")
        self.lbl_rotation_info.pack(side=tk.LEFT, padx=(10, 0))

        # 主按钮区域
        btn_frame = tk.Frame(control_frame, bg="#2c3e50")
        btn_frame.pack(pady=5)

        # X 坐标校正
        tk.Label(correction_frame, text="X:", font=("微软雅黑", 9),
                 fg="white", bg="#2c3e50").pack(side=tk.LEFT, padx=(5, 2))
        self.entry_corr_x = tk.Entry(correction_frame, width=8, font=("微软雅黑", 9))
        self.entry_corr_x.pack(side=tk.LEFT, padx=(0, 5))
        self.entry_corr_x.insert(0, "0.0")

        # Y 坐标校正
        tk.Label(correction_frame, text="Y:", font=("微软雅黑", 9),
                 fg="white", bg="#2c3e50").pack(side=tk.LEFT, padx=(5, 2))
        self.entry_corr_y = tk.Entry(correction_frame, width=8, font=("微软雅黑", 9))
        self.entry_corr_y.pack(side=tk.LEFT, padx=(0, 5))
        self.entry_corr_y.insert(0, "0.0")

        # Z 坐标校正
        tk.Label(correction_frame, text="Z:", font=("微软雅黑", 9),
                 fg="white", bg="#2c3e50").pack(side=tk.LEFT, padx=(5, 2))
        self.entry_corr_z = tk.Entry(correction_frame, width=8, font=("微软雅黑", 9))
        self.entry_corr_z.pack(side=tk.LEFT, padx=(0, 5))
        self.entry_corr_z.insert(0, "0.0")

        # Rx 旋转校正
        tk.Label(correction_frame, text="Rx:", font=("微软雅黑", 9),
                 fg="white", bg="#2c3e50").pack(side=tk.LEFT, padx=(5, 2))
        self.entry_corr_rx = tk.Entry(correction_frame, width=8, font=("微软雅黑", 9))
        self.entry_corr_rx.pack(side=tk.LEFT, padx=(0, 5))
        self.entry_corr_rx.insert(0, "0.0")

        # Ry 旋转校正
        tk.Label(correction_frame, text="Ry:", font=("微软雅黑", 9),
                 fg="white", bg="#2c3e50").pack(side=tk.LEFT, padx=(5, 2))
        self.entry_corr_ry = tk.Entry(correction_frame, width=8, font=("微软雅黑", 9))
        self.entry_corr_ry.pack(side=tk.LEFT, padx=(0, 5))
        self.entry_corr_ry.insert(0, "0.0")

        # Rz 旋转校正
        tk.Label(correction_frame, text="Rz:", font=("微软雅黑", 9),
                 fg="white", bg="#2c3e50").pack(side=tk.LEFT, padx=(5, 2))
        self.entry_corr_rz = tk.Entry(correction_frame, width=8, font=("微软雅黑", 9))
        self.entry_corr_rz.pack(side=tk.LEFT, padx=(0, 10))
        self.entry_corr_rz.insert(0, "0.0")

        # 应用校正按钮
        tk.Button(correction_frame, text="应用校正", command=self.apply_correction,
                  bg="#8e44ad", fg="white", padx=10, pady=2, font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=5)

        # 清除校正按钮
        tk.Button(correction_frame, text="清除校正", command=self.clear_correction,
                  bg="#7f8c8d", fg="white", padx=10, pady=2, font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=5)

        # 保存校正配置按钮
        tk.Button(correction_frame, text="保存配置", command=self.save_correction_config,
                  bg="#27ae60", fg="white", padx=10, pady=2, font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=5)

        # 加载校正配置按钮
        tk.Button(correction_frame, text="加载配置", command=self.load_correction_config,
                  bg="#2980b9", fg="white", padx=10, pady=2, font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=5)

        # 校正状态标签
        self.lbl_correction_status = tk.Label(correction_frame, text="校正: 未应用",
                                              font=("微软雅黑", 9), fg="#f1c40f", bg="#2c3e50")
        self.lbl_correction_status.pack(side=tk.LEFT, padx=(10, 0))

        btn_frame = tk.Frame(control_frame, bg="#2c3e50")
        btn_frame.pack(pady=5)

        tk.Button(btn_frame, text="🔌 连接 CATIA", command=self.connect_catia,
                  bg="#3498db", fg="white", padx=15, pady=5).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="🔫 选择模板", command=self.select_gun_template,
                  bg="#e67e22", fg="white", padx=15, pady=5).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="➕ 添加行", command=self.add_row,
                  bg="#27ae60", fg="white", padx=15, pady=5).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="📋 粘贴Excel", command=self.paste_excel,
                  bg="#16a085", fg="white", padx=15, pady=5).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="🧪 测试插入", command=self.test_single_insert,
                  bg="#9b59b6", fg="white", padx=15, pady=5).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="🚀 批量插入", command=self.start_batch_placement,
                  bg="#e74c3c", fg="white", padx=20, pady=5, font=("", 10, "bold")).pack(side=tk.LEFT, padx=5)

        # 提示信息
        tip_frame = tk.Frame(control_frame, bg="#2c3e50")
        tip_frame.pack(pady=(5, 0))

        tk.Label(tip_frame, text="💡 提示: 双击单元格编辑 | 右键粘贴数据 | Ctrl+V 快速粘贴 | 支持从 Excel 复制粘贴",
                 font=("微软雅黑", 9), fg="#ecf0f1", bg="#2c3e50").pack()

        # 主体区域
        main_pane = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashrelief=tk.RAISED, sashwidth=4)
        main_pane.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 左侧: 可编辑数据表
        left_frame = tk.Frame(main_pane)
        main_pane.add(left_frame, width=850)

        tk.Label(left_frame, text="📋 插枪数据 (可编辑)",
                 font=("微软雅黑", 11, "bold")).pack(anchor="w", pady=5)

        tree_frame = tk.Frame(left_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        # 使用可编辑的 Treeview
        self.data_tree = EditableTreeview(
            tree_frame,
            columns=("Name", "Type", "X", "Y", "Z", "Rx", "Ry", "Rz", "Status"),
            show="headings",
            height=25
        )

        # 设置列标题和宽度
        headers = {
            "Name": ("名称", 150),
            "Type": ("类型", 120),
            "X": ("X", 80),
            "Y": ("Y", 80),
            "Z": ("Z", 80),
            "Rx": ("Rx (°)", 80),
            "Ry": ("Ry (°)", 80),
            "Rz": ("Rz (°)", 80),
            "Status": ("状态", 120)
        }

        for col, (text, width) in headers.items():
            self.data_tree.heading(col, text=text)
            self.data_tree.column(col, width=width, anchor="center")

        scrollbar_y = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.data_tree.yview)
        scrollbar_x = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.data_tree.xview)
        self.data_tree.configure(yscroll=scrollbar_y.set, xscroll=scrollbar_x.set)

        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.data_tree.pack(fill=tk.BOTH, expand=True)

        # 右侧: 配置和控制
        right_frame = tk.Frame(main_pane, bg="#ecf0f1", padx=15, pady=10)
        main_pane.add(right_frame, width=350)

        # 配置区
        tk.Label(right_frame, text="⚙️ 配置", font=("微软雅黑", 11, "bold"),
                 bg="#ecf0f1").pack(anchor="w", pady=(0, 10))

        config_frame = tk.LabelFrame(right_frame, text="文件路径", bg="#ecf0f1", pady=5)
        config_frame.pack(fill=tk.X, pady=5)

        tk.Label(config_frame, text="焊枪模板:", bg="#ecf0f1").pack(anchor="w", padx=5)
        self.lbl_template = tk.Label(config_frame, text="未选择", bg="white", anchor="w",
                                     relief=tk.SUNKEN, padx=5, pady=2, wraplength=320)
        self.lbl_template.pack(fill=tk.X, padx=5, pady=2)

        # 统计信息
        stats_frame = tk.LabelFrame(right_frame, text="统计信息", bg="#ecf0f1", pady=5)
        stats_frame.pack(fill=tk.X, pady=10)

        self.lbl_total = tk.Label(stats_frame, text="总数: 0", bg="#ecf0f1", font=("", 10))
        self.lbl_total.pack(anchor="w", padx=10, pady=2)
        self.lbl_success = tk.Label(stats_frame, text="成功: 0", bg="#ecf0f1", font=("", 10), fg="green")
        self.lbl_success.pack(anchor="w", padx=10, pady=2)
        self.lbl_failed = tk.Label(stats_frame, text="失败: 0", bg="#ecf0f1", font=("", 10), fg="red")
        self.lbl_failed.pack(anchor="w", padx=10, pady=2)

        # === 新增：类型筛选选项 ===
        filter_frame = tk.LabelFrame(right_frame, text="插入筛选", bg="#ecf0f1", pady=5)
        filter_frame.pack(fill=tk.X, pady=10)

        tk.Label(filter_frame, text="选择要插入的点类型:", bg="#ecf0f1",
                 font=("微软雅黑", 9, "bold")).pack(anchor="w", padx=10, pady=(5, 2))

        # 复选框变量
        self.filter_all = tk.BooleanVar(value=True)
        self.filter_weld = tk.BooleanVar(value=False)
        self.filter_via = tk.BooleanVar(value=False)
        self.filter_continuous = tk.BooleanVar(value=False)

        # 全部点
        cb_all = tk.Checkbutton(filter_frame, text="✓ 插入全部点", variable=self.filter_all,
                                bg="#ecf0f1", command=self.on_filter_all_changed)
        cb_all.pack(anchor="w", padx=15, pady=2)

        # 焊点
        cb_weld = tk.Checkbutton(filter_frame, text="🔥 仅插入焊点 (PmWeldLocationOperation)",
                                 variable=self.filter_weld, bg="#ecf0f1",
                                 command=self.on_filter_type_changed)
        cb_weld.pack(anchor="w", padx=15, pady=2)

        # 过渡点
        cb_via = tk.Checkbutton(filter_frame, text="🔄 仅插入过渡点 (PmViaLocationOperation)",
                                variable=self.filter_via, bg="#ecf0f1",
                                command=self.on_filter_type_changed)
        cb_via.pack(anchor="w", padx=15, pady=2)

        # 连续点
        cb_continuous = tk.Checkbutton(filter_frame, text="➰ 仅插入连续点 (PmContinuousFeatureOperation)",
                                       variable=self.filter_continuous, bg="#ecf0f1",
                                       command=self.on_filter_type_changed)
        cb_continuous.pack(anchor="w", padx=15, pady=2)

        # 提示信息
        self.lbl_filter_info = tk.Label(filter_frame, text="当前: 插入全部点",
                                        bg="#ecf0f1", fg="#2980b9", font=("", 8))
        self.lbl_filter_info.pack(anchor="w", padx=15, pady=(5, 2))

        # 进度条
        tk.Label(right_frame, text="📊 执行进度", font=("微软雅黑", 11, "bold"),
                 bg="#ecf0f1").pack(anchor="w", pady=(10, 5))

        self.progress_bar = ttk.Progressbar(right_frame, mode='determinate', length=300)
        self.progress_bar.pack(fill=tk.X, pady=5)

        self.lbl_progress = tk.Label(right_frame, text="等待开始...", bg="#ecf0f1",
                                     font=("", 9), fg="#7f8c8d")
        self.lbl_progress.pack(anchor="w")

        # 操作日志
        tk.Label(right_frame, text="📝 操作日志", font=("微软雅黑", 11, "bold"),
                 bg="#ecf0f1").pack(anchor="w", pady=(15, 5))

        log_frame = tk.Frame(right_frame)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.log_text = tk.Text(log_frame, height=10, wrap=tk.WORD, font=("Consolas", 9))
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscroll=log_scroll.set)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 状态栏
        self.status_label = tk.Label(right_frame, text="就绪", bg="#34495e", fg="white",
                                     anchor="w", padx=10, pady=5, font=("", 9))
        self.status_label.pack(fill=tk.X, side=tk.BOTTOM, pady=(10, 0))

        # 帮助按钮
        tk.Button(right_frame, text="❓ 使用说明", command=self.show_help,
                  bg="#95a5a6", fg="white", padx=10).pack(fill=tk.X, pady=(5, 0))

        # 添加示例数据
        self.add_sample_data()

    def on_rotation_order_changed(self):
        """旋转顺序改变"""
        order = self.rotation_order_var.get()
        self.gun_placer.set_rotation_order(order)

        # 更新显示
        order_names = {
            'kuka': '库卡 (ZYX)',
            'catia_zyx': 'CATIA ZYX',
            'catia_xyz': 'CATIA XYZ'
        }
        self.lbl_rotation_info.config(text=f"当前: {order_names.get(order, order)}")

        if order == 'kuka':
            info_text = "库卡标准：A(绕Z), B(绕Y), C(绕X) | 顺序：Z-Y-X"
        elif order == 'catia_zyx':
            info_text = "CATIA ZYX：顺序：Z-Y-X"
        elif order == 'catia_xyz':
            info_text = "CATIA XYZ：顺序：X-Y-Z"
        else:
            info_text = "未知旋转顺序"

        self.log(f"旋转顺序已切换为: {order_names.get(order, order)}")
        self.log(f"  {info_text}")

    def apply_correction(self):
        """应用坐标校正到当前所有数据"""
        try:
            # 获取校正值
            corr_x = float(self.entry_corr_x.get() or 0)
            corr_y = float(self.entry_corr_y.get() or 0)
            corr_z = float(self.entry_corr_z.get() or 0)
            corr_rx = float(self.entry_corr_rx.get() or 0)
            corr_ry = float(self.entry_corr_ry.get() or 0)
            corr_rz = float(self.entry_corr_rz.get() or 0)

            # 获取所有数据
            data = self.data_tree.get_all_data()

            if not data:
                messagebox.showwarning("警告", "没有数据可应用校正")
                return

            # 更新表格数据
            for item in self.data_tree.get_children():
                values = list(self.data_tree.item(item, 'values'))

                if len(values) >= 8:
                    try:
                        # 应用坐标校正
                        x = float(values[2]) + corr_x
                        y = float(values[3]) + corr_y
                        z = float(values[4]) + corr_z

                        # 应用旋转校正
                        rx = float(values[5]) + corr_rx
                        ry = float(values[6]) + corr_ry
                        rz = float(values[7]) + corr_rz

                        # 更新值
                        values[2] = str(round(x, 3))
                        values[3] = str(round(y, 3))
                        values[4] = str(round(z, 3))
                        values[5] = str(round(rx, 3))
                        values[6] = str(round(ry, 3))
                        values[7] = str(round(rz, 3))

                        # 更新状态
                        values[8] = "已校正"

                        self.data_tree.item(item, values=values)
                    except ValueError:
                        continue

            self.lbl_correction_status.config(
                text=f"校正已应用: X:{corr_x}, Y:{corr_y}, Z:{corr_z}, Rx:{corr_rx}, Ry:{corr_ry}, Rz:{corr_rz}")
            self.log(f"✅ 坐标校正已应用到 {len(data)} 行数据")

        except ValueError:
            messagebox.showerror("错误", "请输入有效的数字")

    def clear_correction(self):
        """清除校正"""
        # 清空输入框
        self.entry_corr_x.delete(0, tk.END)
        self.entry_corr_x.insert(0, "0.0")
        self.entry_corr_y.delete(0, tk.END)
        self.entry_corr_y.insert(0, "0.0")
        self.entry_corr_z.delete(0, tk.END)
        self.entry_corr_z.insert(0, "0.0")
        self.entry_corr_rx.delete(0, tk.END)
        self.entry_corr_rx.insert(0, "0.0")
        self.entry_corr_ry.delete(0, tk.END)
        self.entry_corr_ry.insert(0, "0.0")
        self.entry_corr_rz.delete(0, tk.END)
        self.entry_corr_rz.insert(0, "0.0")

        self.lbl_correction_status.config(text="校正: 已清除")
        self.log("🔄 坐标校正已清除")

    def save_correction_config(self):
        """保存校正配置到文件"""
        try:
            config = {
                'corr_x': self.entry_corr_x.get(),
                'corr_y': self.entry_corr_y.get(),
                'corr_z': self.entry_corr_z.get(),
                'corr_rx': self.entry_corr_rx.get(),
                'corr_ry': self.entry_corr_ry.get(),
                'corr_rz': self.entry_corr_rz.get(),
                'gun_template': self.gun_placer.gun_template_path
            }

            import json
            import os
            from datetime import datetime

            # 创建配置目录
            config_dir = os.path.join(os.path.expanduser("~"), ".catia_gun_tool")
            if not os.path.exists(config_dir):
                os.makedirs(config_dir)

            # 生成文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"correction_config_{timestamp}.json"
            filepath = os.path.join(config_dir, filename)

            # 保存配置
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)

            self.log(f"✅ 配置已保存到: {filepath}")
            messagebox.showinfo("成功", f"配置已保存到:\n{filepath}")

        except Exception as e:
            self.log(f"❌ 保存配置失败: {e}")
            messagebox.showerror("错误", f"保存配置失败:\n{str(e)}")

    def load_correction_config(self):
        """从文件加载校正配置"""
        file_path = filedialog.askopenfilename(
            title="选择配置文件",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")]
        )

        if not file_path:
            return

        try:
            import json

            with open(file_path, 'r', encoding='utf-8') as f:
                config = json.load(f)

            # 加载校正值
            if 'corr_x' in config:
                self.entry_corr_x.delete(0, tk.END)
                self.entry_corr_x.insert(0, config['corr_x'])

            if 'corr_y' in config:
                self.entry_corr_y.delete(0, tk.END)
                self.entry_corr_y.insert(0, config['corr_y'])

            if 'corr_z' in config:
                self.entry_corr_z.delete(0, tk.END)
                self.entry_corr_z.insert(0, config['corr_z'])

            if 'corr_rx' in config:
                self.entry_corr_rx.delete(0, tk.END)
                self.entry_corr_rx.insert(0, config['corr_rx'])

            if 'corr_ry' in config:
                self.entry_corr_ry.delete(0, tk.END)
                self.entry_corr_ry.insert(0, config['corr_ry'])

            if 'corr_rz' in config:
                self.entry_corr_rz.delete(0, tk.END)
                self.entry_corr_rz.insert(0, config['corr_rz'])

            # 加载模板文件路径
            if 'gun_template' in config and config['gun_template']:
                if os.path.exists(config['gun_template']):
                    self.gun_placer.gun_template_path = config['gun_template']
                    self.lbl_template.config(text=config['gun_template'])
                    self.log(f"✅ 已加载模板: {os.path.basename(config['gun_template'])}")

            self.lbl_correction_status.config(text="校正: 已加载配置")
            self.log(f"✅ 配置已从文件加载: {os.path.basename(file_path)}")

        except Exception as e:
            self.log(f"❌ 加载配置失败: {e}")
            messagebox.showerror("错误", f"加载配置失败:\n{str(e)}")

    def get_correction_values(self):
        """获取当前的校正值"""
        try:
            return {
                'X': float(self.entry_corr_x.get() or 0),
                'Y': float(self.entry_corr_y.get() or 0),
                'Z': float(self.entry_corr_z.get() or 0),
                'Rx': float(self.entry_corr_rx.get() or 0),
                'Ry': float(self.entry_corr_ry.get() or 0),
                'Rz': float(self.entry_corr_rz.get() or 0)
            }
        except ValueError:
            return {'X': 0, 'Y': 0, 'Z': 0, 'Rx': 0, 'Ry': 0, 'Rz': 0}

    def add_sample_data(self):
        """添加示例数据"""
        sample_data = [
            ("Gun_001", "PmWeldLocationOperation", "100", "200", "300", "0", "0", "0", "待插入"),
            ("Gun_002", "PmViaLocationOperation", "150", "250", "350", "0", "90", "0", "待插入"),
            ("Gun_003", "PmContinuousFeatureOperation", "200", "300", "400", "90", "0", "0", "待插入"),
        ]
        for data in sample_data:
            self.data_tree.insert("", tk.END, values=data)

        self.update_statistics()

    def log(self, message: str):
        """添加日志"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.update()

    def connect_catia(self):
        """连接 CATIA"""
        try:
            self.log("正在连接 CATIA...")

            if PYCATIA_AVAILABLE:
                from pycatia import catia as pycatia_catia
                self.catia_app = pycatia_catia()
                conn_type = 'pycatia'
            else:
                import win32com.client
                try:
                    self.catia_app = win32com.client.GetActiveObject("CATIA.Application")
                except:
                    self.catia_app = win32com.client.Dispatch("CATIA.Application")
                    self.catia_app.Visible = True
                conn_type = 'win32com'

            self.gun_placer.set_catia_app(self.catia_app, conn_type)
            self.status_label.config(text=f"✅ CATIA 已连接 ({conn_type})")
            self.log(f"✅ CATIA 连接成功 ({conn_type})")
            messagebox.showinfo("成功", "CATIA 连接成功")

        except Exception as e:
            self.log(f"❌ CATIA 连接失败: {e}")
            self.status_label.config(text="❌ CATIA 连接失败")
            messagebox.showerror("连接失败", f"无法连接 CATIA:\n{str(e)}")

    def try_auto_connect_catia(self):
        """启动时自动连接（只连接已运行的CATIA，不创建新进程）"""
        try:
            if PYCATIA_AVAILABLE:
                # pycatia 方式 - 只连接现有实例
                try:
                    from pycatia import catia as pycatia_catia
                    # pycatia.catia() 默认会尝试 GetActiveObject，失败后会 Dispatch
                    # 我们需要手动控制，只获取现有实例
                    import win32com.client
                    catia_com = win32com.client.GetActiveObject("CATIA.Application")
                    # 成功获取，包装为 pycatia 对象
                    self.catia_app = pycatia_catia()
                    conn_type = 'pycatia'
                    self.gun_placer.set_catia_app(self.catia_app, conn_type)
                    self.status_label.config(text=f"✅ CATIA 已连接 ({conn_type})")
                    return
                except Exception as e:
                    # CATIA 未运行，不做任何事
                    self.status_label.config(text="CATIA 未开启")
                    return
            else:
                # win32com 方式 - 只连接现有实例
                import win32com.client
                try:
                    # 只尝试 GetActiveObject，不使用 Dispatch
                    self.catia_app = win32com.client.GetActiveObject("CATIA.Application")
                    conn_type = 'win32com'
                    self.gun_placer.set_catia_app(self.catia_app, conn_type)
                    self.status_label.config(text=f"✅ CATIA 已连接 ({conn_type})")
                    return
                except Exception as e:
                    # CATIA 未运行，不做任何事
                    self.status_label.config(text="CATIA 未开启")
                    return

        except Exception as e:
            print(f"自动连接失败: {e}")
            self.status_label.config(text="CATIA 未开启")

    def select_gun_template(self):
        """选择焊枪模板文件"""
        file_path = filedialog.askopenfilename(
            title="选择焊枪模板文件",
            filetypes=[
                ("CATIA 文件", "*.CATPart *.CATProduct *.cgr"),
                ("CATIA Part", "*.CATPart"),
                ("CATIA Product", "*.CATProduct"),
                ("CGR 文件", "*.cgr"),
                ("所有文件", "*.*")
            ]
        )

        if file_path:
            self.gun_placer.gun_template_path = file_path
            self.lbl_template.config(text=file_path)

            # 显示文件类型
            file_ext = os.path.splitext(file_path)[1]
            self.log(f"✅ 已选择模板: {os.path.basename(file_path)}")
            self.log(f"   文件类型: {file_ext}")

    def add_row(self):
        """添加新行"""
        self.data_tree.add_row()
        self.update_statistics()

    def paste_excel(self):
        """从 Excel 文件粘贴"""
        self.data_tree.paste_from_excel()
        self.update_statistics()

    def on_filter_all_changed(self):
        """全部点复选框改变"""
        if self.filter_all.get():
            # 勾选全部，取消其他选项
            self.filter_weld.set(False)
            self.filter_via.set(False)
            self.filter_continuous.set(False)
            self.lbl_filter_info.config(text="当前: 插入全部点")
        else:
            # 取消全部，什么都不选
            self.lbl_filter_info.config(text="当前: 未选择任何类型")

    def on_filter_type_changed(self):
        """类型筛选改变"""
        if self.filter_weld.get() or self.filter_via.get() or self.filter_continuous.get():
            # 有类型被选中，取消全部
            self.filter_all.set(False)

            # 更新提示信息
            selected = []
            if self.filter_weld.get():
                selected.append("焊点")
            if self.filter_via.get():
                selected.append("过渡点")
            if self.filter_continuous.get():
                selected.append("连续点")

            self.lbl_filter_info.config(text=f"当前: 插入 {'+'.join(selected)}")
        else:
            # 没有类型被选中，自动勾选全部
            self.filter_all.set(True)
            self.lbl_filter_info.config(text="当前: 插入全部点")

    def update_statistics(self):
        """更新统计信息"""
        total = len(self.data_tree.get_children())
        self.lbl_total.config(text=f"总数: {total}")

    def progress_callback(self, current, total, message):
        """进度回调"""
        progress = int((current / total) * 100)
        self.progress_bar['value'] = progress
        self.lbl_progress.config(text=f"{message} ({current}/{total})")
        self.log(message)
        self.update()

    def test_single_insert(self):
        """测试单个文件插入（用于调试）"""
        if not self.gun_placer.is_catia_connected():
            messagebox.showwarning("警告", "请先连接 CATIA")
            return

        if not self.gun_placer.has_active_product():
            messagebox.showwarning("警告", "请在 CATIA 中打开一个 Product 文档")
            return

        if not self.gun_placer.gun_template_path:
            messagebox.showwarning("警告", "请先选择焊枪模板文件")
            return

        try:
            self.log("=" * 50)
            self.log("🧪 开始测试插入...")

            # 规范化路径
            template_path = os.path.normpath(self.gun_placer.gun_template_path)
            template_path = template_path.replace('/', '\\')

            self.log(f"模板文件: {template_path}")
            self.log(f"文件存在: {os.path.exists(template_path)}")
            self.log(f"文件大小: {os.path.getsize(template_path) / 1024:.2f} KB")

            # 获取 Product
            if self.gun_placer.connection_type == 'pycatia':
                doc = self.catia_app.active_document
                product = doc.product
                products = product.products
                com_products = products.com_object
            else:
                doc = self.catia_app.ActiveDocument
                product = doc.Product
                products = product.Products
                com_products = products

            count_before = com_products.Count
            self.log(f"插入前产品数量: {count_before}")

            # 尝试插入
            self.log("正在调用 AddComponentsFromFiles...")
            file_array = (template_path,)

            try:
                com_products.AddComponentsFromFiles(file_array, "All")
                self.log("✓ API 调用成功（无异常）")
            except Exception as e:
                self.log(f"✗ API 调用失败: {e}")
                return

            # 等待 CATIA 处理
            import time
            time.sleep(0.5)

            count_after = com_products.Count
            self.log(f"插入后产品数量: {count_after}")

            if count_after > count_before:
                self.log(f"✓ 成功添加了 {count_after - count_before} 个产品")

                # 尝试获取新产品
                try:
                    new_product = com_products.Item(count_after)
                    self.log(f"✓ 成功获取产品: {new_product.Name}")
                except Exception as e:
                    self.log(f"✗ 无法获取产品: {e}")
            else:
                self.log("✗ 产品数量没有增加")
                self.log("可能原因:")
                self.log("  1. 文件路径有问题（检查特殊字符、空格）")
                self.log("  2. 文件格式不正确或已损坏")
                self.log("  3. 文件已在 CATIA 中打开")
                self.log("  4. CATIA 显示了错误对话框（请检查）")

            self.log("=" * 50)

        except Exception as e:
            self.log(f"测试失败: {e}")
            traceback.print_exc()

    def start_batch_placement(self):
        """开始批量插入"""
        # 检查前置条件
        if not self.gun_placer.is_catia_connected():
            messagebox.showwarning("警告", "请先连接 CATIA")
            return

        if not self.gun_placer.has_active_product():
            messagebox.showwarning("警告", "请在 CATIA 中打开一个 Product 文档")
            return

        if not self.gun_placer.gun_template_path:
            messagebox.showwarning("警告", "请先选择焊枪模板文件")
            return

        # 获取表格数据
        all_gun_data = self.data_tree.get_all_data()
        if not all_gun_data:
            messagebox.showwarning("警告", "没有有效的插枪数据")
            return

        # === 新增：获取校正值 ===
        correction_values = self.get_correction_values()
        apply_correction = any(value != 0 for value in correction_values.values())

        if apply_correction:
            self.log(f"📐 将应用坐标校正: {correction_values}")

        # 记录当前旋转顺序
        rotation_order = self.rotation_order_var.get()
        self.log(f"🔄 使用旋转顺序: {rotation_order}")

        # === 根据筛选条件过滤数据 ===
        gun_data = []

        if self.filter_all.get():
            # 插入全部点
            gun_data = all_gun_data
            filter_desc = "全部点"
        else:
            # 根据类型筛选
            selected_types = []
            if self.filter_weld.get():
                selected_types.append("PmWeldLocationOperation")
            if self.filter_via.get():
                selected_types.append("PmViaLocationOperation")
            if self.filter_continuous.get():
                selected_types.append("PmContinuousFeatureOperation")

            if not selected_types:
                messagebox.showwarning("警告", "请至少选择一种点类型")
                return

            # 过滤数据
            gun_data = [g for g in all_gun_data if g['Type'] in selected_types]

            # 生成描述
            type_names = []
            if self.filter_weld.get():
                type_names.append("焊点")
            if self.filter_via.get():
                type_names.append("过渡点")
            if self.filter_continuous.get():
                type_names.append("连续点")
            filter_desc = " + ".join(type_names)

        if not gun_data:
            messagebox.showwarning("警告", f"筛选后没有数据\n\n当前筛选: {filter_desc}")
            return

        # 确认执行
        confirm_msg = f"即将插入 {len(gun_data)} 个组件（共 {len(all_gun_data)} 个）\n\n"
        confirm_msg += f"筛选条件: {filter_desc}\n"
        confirm_msg += f"模板: {os.path.basename(self.gun_placer.gun_template_path)}\n"

        if apply_correction:
            confirm_msg += f"坐标校正: {correction_values}\n\n"
        else:
            confirm_msg += "\n"

        confirm_msg += "是否继续?"

        if not messagebox.askyesno("确认", confirm_msg):
            return

        # 重置进度
        self.progress_bar['value'] = 0
        self.lbl_success.config(text="成功: 0")
        self.lbl_failed.config(text="失败: 0")

        self.log("=" * 50)
        self.log(f"🚀 开始批量插入 ({filter_desc})...")
        self.log(f"总数据: {len(all_gun_data)} | 筛选后: {len(gun_data)}")

        if apply_correction:
            self.log(f"📐 应用坐标校正: {correction_values}")

        # 执行插入（传递校正值）
        success, failed, status_list = self.gun_placer.place_guns_batch(
            self.gun_placer.gun_template_path,
            gun_data,
            self.progress_callback,
            correction_values if apply_correction else None,
            rotation_order  # 传递旋转顺序
        )

        # 更新表格状态（只更新被插入的行）
        all_data_idx = 0
        status_idx = 0
        for gun in all_gun_data:
            if gun in gun_data:
                # 这一行被插入了
                self.data_tree.update_status(all_data_idx, status_list[status_idx])
                status_idx += 1
            all_data_idx += 1

        # 更新统计
        self.lbl_success.config(text=f"成功: {success}")
        self.lbl_failed.config(text=f"失败: {failed}")

        self.log("=" * 50)
        self.log(f"✅ 插入完成 | 成功: {success} | 失败: {failed}")

        if apply_correction:
            self.log(f"📐 坐标校正已应用")

        # 结果提示
        result_msg = f"批量插入完成!\n\n"
        result_msg += f"筛选条件: {filter_desc}\n"
        result_msg += f"成功: {success}\n"
        result_msg += f"失败: {failed}\n"

        if apply_correction:
            result_msg += f"坐标校正: {correction_values}"

        messagebox.showinfo("完成", result_msg)

    def show_help(self):
        """显示使用说明"""
        help_text = """
📖 CATIA 焊枪批量插入工具使用说明

1️⃣ 数据输入方式

  方式一: 手动编辑
  • 双击单元格直接编辑
  • 点击"添加行"按钮添加新数据

  方式二: 从 Excel 粘贴
  • 在 Excel 中复制数据 (Ctrl+C)
  • 在表格中按 Ctrl+V 粘贴
  • 或右键选择"粘贴"

  方式三: 导入 Excel 文件
  • 点击"粘贴Excel"按钮
  • 选择 Excel 文件导入

2️⃣ 数据格式说明

  Name: 焊枪名称
  Type: 点类型（见下方说明）
  X, Y, Z: 位置坐标 (单位: mm)
  Rx, Ry, Rz: 欧拉角旋转 (单位: 度)

  示例:
  Gun_001 | PmWeldLocationOperation | 100 | 200 | 300 | 0 | 0 | 0

3️⃣ 点类型说明

  PmWeldLocationOperation
    → 焊点 (🔥)

  PmViaLocationOperation
    → 过渡点 (🔄)

  PmContinuousFeatureOperation
    → 连续点 (➰)

4️⃣ 插入筛选功能

  勾选框选项:
  ✓ 插入全部点 - 不筛选，全部插入
  🔥 仅插入焊点 - 只插入焊点类型
  🔄 仅插入过渡点 - 只插入过渡点类型
  ➰ 仅插入连续点 - 只插入连续点类型

  可以组合选择:
  • 同时勾选焊点+过渡点
  • 同时勾选焊点+连续点
  • 等等...

5️⃣ 支持的文件格式

  ✓ .CATPart    - CATIA Part 文件
  ✓ .CATProduct - CATIA Product 文件
  ✓ .cgr        - CGR 可视化文件

  注意: 只需选择一个模板文件，程序会
       为每个焊枪插入该模板的副本

6️⃣ 插入逻辑

  ① 自动创建容器产品
     • 名称格式: "产品名-GUN-日期"
     • 例如: Product1-GUN-20250207

  ② 在容器中批量插入
     • 根据筛选条件选择要插入的点
     • 插入选定的模板文件
     • 自动重命名为指定名称

  ③ 设置位置和姿态
     • 根据 X,Y,Z 和 Rx,Ry,Rz 设置
     • 使用欧拉角转换为旋转矩阵

7️⃣ 操作流程

  ① 打开 CATIA，创建或打开 Product 文档
  ② 点击"连接 CATIA"
  ③ 点击"选择模板"，选择焊枪文件
  ④ 输入或粘贴插枪数据（含Type列）
  ⑤ 选择要插入的点类型（筛选）
  ⑥ 点击"批量插入"

8️⃣ 快捷键

  Ctrl+V: 粘贴数据
  双击: 编辑单元格
  右键: 显示菜单

9️⃣ 注意事项

  • 确保 CATIA 中有活动的 Product 文档
  • 模板文件路径不要包含中文
  • 插入过程中不要操作 CATIA
  • 欧拉角采用 ZYX 旋转顺规
  • 程序不会自动启动 CATIA
  • Type列必须使用正确的类型名称
        """

        messagebox.showinfo("使用说明", help_text)



# ==================== GUI 界面类 ====================
# ---------------------------
# ToolBoxPage (Tkinter UI)
# ---------------------------

class ToolBoxPage(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg="#ecf0f1")

        self.catia_app = None

        # 检查 pycatia 是否可用
        if not PYCATIA_AVAILABLE:
            messagebox.showerror("错误", "pycatia 库未安装！\n\n请运行: pip install pycatia")

        # 使用业务逻辑类
        self.weld_extractor = CATIAWeldPointExtractor()
        self.sphere_scanner = CATIASphereScanner()

        self.setup_ui()

        self.try_auto_connect_catia()

    def setup_ui(self):
        # 标题栏
        title_frame = tk.Frame(self, bg="#34495e", pady=10)
        title_frame.pack(fill=tk.X)

        self.status_label = tk.Label(self, text="CATIA 未开启", bg="#34495e", fg="white", anchor="e")
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)

        title_text = "🛠️ CATIA 焊点处理工具"
        if not PYCATIA_AVAILABLE:
            title_text += " - ⚠️ pycatia 未安装"

        tk.Label(title_frame, text=title_text,
                 font=("微软雅黑", 15, "bold"), bg="#34495e", fg="white").pack()

        main_frame = tk.Frame(self, bg="#ecf0f1")
        main_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=20)

        # === 工具1：焊点导出 ===
        weld_frame = tk.LabelFrame(main_frame, text="📍 焊点数据导出",
                                   font=("微软雅黑", 12, "bold"),
                                   bg="#ecf0f1", padx=20, pady=15)
        weld_frame.pack(fill=tk.X, pady=(0, 20))

        btn_frame1 = tk.Frame(weld_frame, bg="#ecf0f1")
        btn_frame1.pack(fill=tk.X)

        tk.Button(btn_frame1, text="🔗 连接 CATIA", command=self.connect_catia,
                  bg="#3498db", fg="white", width=15, height=2).pack(side=tk.LEFT, padx=(0, 10))
        tk.Button(btn_frame1, text="📊 提取焊点", command=self.extract_weld_points,
                  bg="#e67e22", fg="white", width=15, height=2).pack(side=tk.LEFT, padx=(0, 10))
        tk.Button(btn_frame1, text="💾 导出 Excel", command=self.export_welds_to_excel,
                  bg="#27ae60", fg="white", width=15, height=2).pack(side=tk.LEFT, padx=(0, 10))
        tk.Button(btn_frame1, text="📥 导入数据", command=self.import_weld_data,
                  bg="#9b59b6", fg="white", width=15, height=2).pack(side=tk.LEFT, padx=(0, 10))

        # 把生成点球按钮和勾选框放在同一个小框里
        ball_frame = tk.Frame(btn_frame1, bg="#ecf0f1")
        ball_frame.pack(side=tk.LEFT, padx=(0, 10))

        tk.Button(ball_frame, text="💾 生成点球", command=self.newball,
                  bg="#27ae60", fg="white", width=15, height=2).pack(side=tk.LEFT)

        self.auto_rename_var = tk.BooleanVar(value=False)
        self.rename_check = tk.Checkbutton(ball_frame, text="按标准自动重命名", variable=self.auto_rename_var,
                                           bg="#ecf0f1")
        self.rename_check.pack(side=tk.LEFT, padx=(5, 0))

        self.weld_tree_frame = tk.Frame(weld_frame, bg="#ecf0f1")
        self.weld_tree_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        columns = ("Class", "ExternalId", "Name", "Location", "X", "Y", "Z", "Type")
        self.weld_tree = ttk.Treeview(self.weld_tree_frame,
                                      columns=columns,
                                      show="headings", height=8)

        for col in columns:
            self.weld_tree.heading(col, text=col)
            self.weld_tree.column(col, width=100 if col != "Location" else 150)

        weld_scrollbar = ttk.Scrollbar(self.weld_tree_frame, orient=tk.VERTICAL, command=self.weld_tree.yview)
        self.weld_tree.configure(yscroll=weld_scrollbar.set)
        weld_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.weld_tree.pack(fill=tk.BOTH, expand=True)

        # === 工具2：几何体重心提取 ===
        sphere_frame = tk.LabelFrame(main_frame, text="🔵 几何体重心提取",
                                     font=("微软雅黑", 12, "bold"),
                                     bg="#ecf0f1", padx=20, pady=15)
        sphere_frame.pack(fill=tk.X)

        btn_frame2 = tk.Frame(sphere_frame, bg="#ecf0f1")
        btn_frame2.pack(fill=tk.X)
        tk.Button(btn_frame2, text="🔍 扫描所有", command=self.scan_all,
                  bg="#9b59b6", fg="white", width=15, height=2).pack(side=tk.LEFT, padx=(0, 10))
        tk.Button(btn_frame2, text="🎯 扫描选中", command=self.scan_selected,
                  bg="#3498db", fg="white", width=15, height=2).pack(side=tk.LEFT, padx=(0, 10))
        tk.Button(btn_frame2, text="📝 生成重心点", command=self.generate_points,
                  bg="#e74c3c", fg="white", width=15, height=2).pack(side=tk.LEFT, padx=(0, 10))
        tk.Button(btn_frame2, text="💾 导出坐标", command=self.export_sphere_coords,
                  bg="#27ae60", fg="white", width=15, height=2).pack(side=tk.LEFT)

        self.sphere_tree_frame = tk.Frame(sphere_frame, bg="#ecf0f1")
        self.sphere_tree_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.sphere_tree = ttk.Treeview(self.sphere_tree_frame,
                                        columns=("Name", "X", "Y", "Z", "Radius"),
                                        show="headings", height=8)
        for col in ["Name", "X", "Y", "Z", "Radius"]:
            self.sphere_tree.heading(col, text=col)
            self.sphere_tree.column(col, width=100)

        sphere_scrollbar = ttk.Scrollbar(self.sphere_tree_frame, orient=tk.VERTICAL, command=self.sphere_tree.yview)
        self.sphere_tree.configure(yscroll=sphere_scrollbar.set)
        sphere_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.sphere_tree.pack(fill=tk.BOTH, expand=True)

        self.status_label = tk.Label(self, text="就绪 | 使用 pycatia", bg="#34495e", fg="white", anchor="w")
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)

    def log_error(self, context, error):
        print(f"Error in {context}: {error}")
        traceback.print_exc()

    def connect_catia(self):
        try:
            if PYCATIA_AVAILABLE:
                from pycatia import catia as pycatia_catia
                self.catia_app = pycatia_catia()
                conn_type = 'pycatia'
            else:
                import win32com.client
                try:
                    self.catia_app = win32com.client.GetActiveObject("CATIA.Application")
                except Exception:
                    self.catia_app = win32com.client.Dispatch("CATIA.Application")
                conn_type = 'win32com'

            # 传递给业务类
            self.weld_extractor.set_catia_app(self.catia_app, conn_type)
            if hasattr(self, 'sphere_scanner') and self.sphere_scanner:
                self.sphere_scanner.set_catia_app(self.catia_app)

            self.status_label.config(text=f"已连接 | CATIA ({conn_type})")
            messagebox.showinfo("成功", f"CATIA 连接成功（{conn_type}）")
        except Exception as e:
            self.log_error("连接 CATIA", e)
            messagebox.showerror("失败", f"无法连接 CATIA\n\n{str(e)}")

    def extract_weld_points(self):
        """提取焊点"""
        if not self.catia_app:
            messagebox.showwarning("警告", "请先连接 CATIA")
            return

        try:
            # 确保提取器已设置
            if not hasattr(self, 'weld_extractor'):
                self.weld_extractor = CATIAWeldPointExtractor()
                if self.catia_app:
                    # 判断连接类型
                    if hasattr(self.catia_app, 'ActiveDocument'):
                        self.weld_extractor.set_catia_app(self.catia_app, 'win32com')
                    elif hasattr(self.catia_app, 'active_document'):
                        self.weld_extractor.set_catia_app(self.catia_app, 'pycatia')

            # 清空表格
            for item in self.weld_tree.get_children():
                self.weld_tree.delete(item)

            should_rename = self.auto_rename_var.get()

            # 提取焊点数据
            weld_data = self.weld_extractor.extract_weld_points(auto_rename=should_rename)

            # 更新表格显示
            for data in weld_data:
                self.weld_tree.insert("", "end", values=(
                    data["Class"], data["ExternalId"], data["Name"],
                    data["Location"], data["X"], data["Y"], data["Z"], data["Type"]
                ))

            # 更新状态
            self.status_label.config(text=self.weld_extractor.get_status())

        except Exception as e:
            self.log_error("提取焊点", e)
            messagebox.showerror("错误", f"发生意外错误: {str(e)}")

    def scan_selected(self):
        """扫描当前选中的几何体重心"""
        if not self.catia_app:
            messagebox.showwarning("警告", "请先连接 CATIA")
            return

        try:
            # 清空表格
            for item in self.sphere_tree.get_children():
                self.sphere_tree.delete(item)

            # 调用业务逻辑扫描选中对象
            sphere_data = self.sphere_scanner.scan_selected_objects()

            if not sphere_data:
                messagebox.showinfo("提示", "请先在 CATIA 中选择要扫描的几何体")
                return

            # 显示到界面
            for sphere in sphere_data:
                self.sphere_tree.insert("", "end", values=(
                    sphere["Name"], sphere["X"], sphere["Y"], sphere["Z"], sphere["Radius"]
                ))

            self.status_label.config(text=f"扫描完成: {len(sphere_data)} 个选中几何体")

        except Exception as e:
            self.log_error("扫描选中几何体", e)
            messagebox.showerror("错误", str(e))

    def scan_all(self):
        """扫描所有几何体重心"""
        if not self.catia_app:
            messagebox.showwarning("警告", "请先连接 CATIA")
            return

        try:
            # 清空表格
            for item in self.sphere_tree.get_children():
                self.sphere_tree.delete(item)

            # 调用业务逻辑
            sphere_data = self.sphere_scanner.scan_all_bodies(search_keywords=None)

            # 显示到界面
            for sphere in sphere_data:
                self.sphere_tree.insert("", "end", values=(
                    sphere["Name"], sphere["X"], sphere["Y"], sphere["Z"], sphere["Radius"]
                ))

            self.status_label.config(text=f"扫描完成: {len(sphere_data)} 个几何体")

        except Exception as e:
            self.log_error("扫描几何体", e)
            messagebox.showerror("错误", str(e))

    def generate_points(self):
        """在扫描到的几何体重心生成点"""
        if not self.catia_app:
            messagebox.showwarning("警告", "请先连接 CATIA")
            return

        sphere_data = self.sphere_scanner.get_data()
        if not sphere_data:
            messagebox.showwarning("警告", "请先扫描几何体")
            return

        try:
            # 调用业务逻辑
            created_count = self.sphere_scanner.generate_points_at_centers("Geometry_COG_Points")
            messagebox.showinfo("完成", f"成功创建 {created_count} 个重心点到 'Geometry_COG_Points'")
        except Exception as e:
            self.log_error("生成点", e)
            messagebox.showerror("错误", str(e))

    def export_welds_to_excel(self):
        weld_points_data = self.weld_extractor.get_data()
        if not weld_points_data:
            messagebox.showwarning("警告", "没有数据可导出")
            return
        getname = self.weld_extractor.catia_getname()
        base = getname.get('document_name_no_ext', 'weld_points')
        self._export_data(weld_points_data, f"{base}.xlsx")

    def newball(self):
        self.weld_extractor.generate_spheres_from_points(parent_tk=self)

    def _convert_to_float(self, value):
        """将各种格式的数值转换为浮点数"""
        if pd.isna(value):
            raise ValueError("坐标值为空")

        if isinstance(value, (int, float)):
            return float(value)

        # 处理字符串
        str_value = str(value).strip()

        # 移除可能的分隔符
        str_value = str_value.replace(',', '')

        # 尝试直接转换
        try:
            return float(str_value)
        except ValueError:
            # 尝试提取数字（如果字符串中包含其他字符）
            import re
            numbers = re.findall(r"[-+]?\d*\.\d+|\d+", str_value)
            if numbers:
                return float(numbers[0])
            else:
                raise ValueError(f"无法转换为数值: {value}")

    def import_weld_data(self):
        """从 Excel/CSV 文件导入焊点数据（仅严格匹配XYZ坐标，其他字段自动生成）"""
        try:
            # 打开文件选择对话框
            file_path = filedialog.askopenfilename(
                title="选择焊点数据文件（仅需X,Y,Z列）",
                filetypes=[
                    ("Excel 文件", "*.xlsx *.xls"),
                    ("CSV 文件", "*.csv"),
                    ("所有文件", "*.*")
                ]
            )

            if not file_path:
                return  # 用户取消选择

            # 清空现有数据
            for item in self.weld_tree.get_children():
                self.weld_tree.delete(item)

            # 清空提取器数据
            self.weld_extractor.weld_points_data = []

            # 读取文件
            try:
                if file_path.endswith(('.xlsx', '.xls')):
                    df = pd.read_excel(file_path)
                else:  # CSV
                    df = pd.read_csv(file_path, encoding='utf-8')
            except Exception as e:
                messagebox.showerror("文件读取失败", f"无法读取文件:\n\n{str(e)}")
                return

            # 检查必需的XYZ列 - 支持多种可能的列名
            x_col = None
            y_col = None
            z_col = None

            # 可能的列名变体
            x_variants = ['X', 'x', 'X坐标', 'x坐标', 'X轴', 'x轴', 'X Value', 'x value']
            y_variants = ['Y', 'y', 'Y坐标', 'y坐标', 'Y轴', 'y轴', 'Y Value', 'y value']
            z_variants = ['Z', 'z', 'Z坐标', 'z坐标', 'Z轴', 'z轴', 'Z Value', 'z value']

            # 查找匹配的列
            for col in df.columns:
                col_str = str(col).strip()
                if col_str in x_variants:
                    x_col = col
                elif col_str in y_variants:
                    y_col = col
                elif col_str in z_variants:
                    z_col = col

            # 如果没找到标准列名，尝试查找包含关键字的列
            if not x_col:
                for col in df.columns:
                    if any(keyword in str(col).lower() for keyword in ['x', 'x坐标', 'x轴']):
                        x_col = col
                        break

            if not y_col:
                for col in df.columns:
                    if any(keyword in str(col).lower() for keyword in ['y', 'y坐标', 'y轴']):
                        y_col = col
                        break

            if not z_col:
                for col in df.columns:
                    if any(keyword in str(col).lower() for keyword in ['z', 'z坐标', 'z轴']):
                        z_col = col
                        break

            # 最终检查
            if not (x_col and y_col and z_col):
                # 显示可用的列供用户参考
                available_cols = "\n".join(df.columns)
                messagebox.showerror("坐标列缺失",
                                     f"无法在文件中找到X、Y、Z坐标列！\n\n"
                                     f"请确保文件包含以下列之一：\n"
                                     f"X列: {', '.join(x_variants)}\n"
                                     f"Y列: {', '.join(y_variants)}\n"
                                     f"Z列: {', '.join(z_variants)}\n\n"
                                     f"当前文件中的列：\n{available_cols}")
                return

            # 获取名称列（如果存在）
            name_col = None
            name_variants = ['Name', 'name', '名称', '点名称', 'Point Name', 'PointName']
            for col in df.columns:
                col_str = str(col).strip()
                if col_str in name_variants:
                    name_col = col
                    break

            # 转换数据格式
            weld_data = []
            successful_count = 0
            failed_count = 0

            for index, row in df.iterrows():
                try:
                    # 提取XYZ坐标
                    try:
                        x_val = self._convert_to_float(row[x_col])
                        y_val = self._convert_to_float(row[y_col])
                        z_val = self._convert_to_float(row[z_col])
                    except (ValueError, TypeError) as e:
                        print(f"❌ 第 {index + 1} 行坐标转换失败: {e}")
                        failed_count += 1
                        continue

                    # 格式化坐标字符串
                    x_str = f"{x_val:.3f}"
                    y_str = f"{y_val:.3f}"
                    z_str = f"{z_val:.3f}"

                    # 生成名称
                    if name_col and pd.notna(row[name_col]):
                        name = str(row[name_col]).strip()
                    else:
                        name = f"WeldPoint_{index + 1:03d}"

                    # 创建焊点数据（所有其他字段自动生成）
                    weld_point = {
                        "Class": "PmWeldPoint",
                        "ExternalId": name,
                        "Name": name,
                        "Location": f"{x_str},{y_str},{z_str}",
                        "X": x_str,
                        "Y": y_str,
                        "Z": z_str,
                        "Type": "焊点" if any(
                            keyword in name.lower() for keyword in ['weld', 'spot', '焊点', '焊接']) else "普通点"
                    }

                    weld_data.append(weld_point)
                    successful_count += 1

                except Exception as e:
                    print(f"❌ 导入第 {index + 1} 行数据失败: {e}")
                    failed_count += 1
                    continue

            if not weld_data:
                messagebox.showwarning("导入失败", "没有成功导入任何焊点数据")
                return

            # 添加到提取器数据
            self.weld_extractor.weld_points_data = weld_data

            # 显示到表格
            for data in weld_data:
                self.weld_tree.insert("", "end", values=(
                    data["Class"], data["ExternalId"], data["Name"],
                    data["Location"], data["X"], data["Y"], data["Z"], data["Type"]
                ))

            # 更新状态
            status_msg = f"已导入 {successful_count} 个焊点"
            if failed_count > 0:
                status_msg += f"（跳过 {failed_count} 个无效数据）"

            self.weld_extractor.status_message = f"已从 {os.path.basename(file_path)} {status_msg}"
            self.status_label.config(text=self.weld_extractor.get_status())

            # 显示导入结果
            result_message = f"""
    导入完成！

    成功导入: {successful_count} 个焊点
    失败跳过: {failed_count} 个数据

    使用的列:
    • X列: {x_col}
    • Y列: {y_col}
    • Z列: {z_col}
    • 名称列: {name_col if name_col else '自动生成'}

    文件: {os.path.basename(file_path)}
    总行数: {len(df)}
            """

            messagebox.showinfo("导入成功", result_message.strip())

        except Exception as e:
            self.log_error("导入焊点数据", e)
            messagebox.showerror("导入失败", f"导入焊点数据时发生错误:\n\n{str(e)}")

    def export_sphere_coords(self):
        sphere_data = self.sphere_scanner.get_data()
        if not sphere_data:
            messagebox.showwarning("警告", "没有数据可导出")
            return
        # 移除内部使用的 _coords 字段
        export_data = [{k: v for k, v in s.items() if k != "_coords"} for s in sphere_data]
        self._export_data(export_data, "几何体重心数据.xlsx")

    def _export_data(self, data, default_name):
        try:
            # 定义支持的文件类型
            file_types = [
                ("Excel文件", "*.xlsx"),
                ("Excel启用宏的文件", "*.xlsm"),
                ("Excel 97-2003文件", "*.xls"),
                ("CSV文件", "*.csv"),
                ("文本文件", "*.txt"),
                ("所有文件", "*.*")
            ]

            f = filedialog.asksaveasfilename(
                initialfile=default_name,
                filetypes=file_types,
                defaultextension=".xlsx"  # 设置默认扩展名
            )

            if not f:
                return  # 用户取消保存

            # 根据文件扩展名选择保存格式
            file_ext = os.path.splitext(f)[1].lower()

            if file_ext == '.csv':
                # CSV格式
                pd.DataFrame(data).to_csv(f, index=False, encoding='utf-8-sig')
            elif file_ext == '.xls':
                # 旧版Excel格式
                pd.DataFrame(data).to_excel(f, index=False, engine='xlwt')
            elif file_ext == '.xlsm':
                # 启用宏的Excel格式
                with pd.ExcelWriter(f, engine='openpyxl') as writer:
                    pd.DataFrame(data).to_excel(writer, index=False, sheet_name='Sheet1')
                    # 如果需要创建启用宏的工作簿，可以添加以下代码
                    # writer.book.save(f)
            elif file_ext == '.txt':
                # 文本文件格式
                pd.DataFrame(data).to_csv(f, index=False, sep='\t', encoding='utf-8')
            else:
                # 默认保存为xlsx格式
                pd.DataFrame(data).to_excel(f, index=False, engine='openpyxl')

            messagebox.showinfo("成功", f"导出完成\n文件已保存到: {f}")

        except PermissionError:
            messagebox.showerror("导出失败", "文件被其他程序占用，请关闭后重试")
        except Exception as e:
            messagebox.showerror("导出失败", f"导出过程中发生错误:\n{str(e)}")

    def try_auto_connect_catia(self):
        """软件启动时自动尝试连接 CATIA"""
        try:
            if PYCATIA_AVAILABLE:
                from pycatia import catia as pycatia_catia
                self.catia_app = pycatia_catia()
                conn_type = 'pycatia'
            else:
                import win32com.client
                try:
                    self.catia_app = win32com.client.GetActiveObject("CATIA.Application")
                    conn_type = 'win32com'
                except Exception:
                    # CATIA 未开启时直接跳过，不创建后台进程
                    self.status_label.config(text="CATIA 未开启")
                    self.catia_app = None
                    return

            # 设置到业务逻辑类
            self.weld_extractor.set_catia_app(self.catia_app, conn_type)
            if self.sphere_scanner:
                self.sphere_scanner.set_catia_app(self.catia_app)

            self.status_label.config(text=f"CATIA 已连接 ({conn_type})")

        except Exception:
            self.status_label.config(text="CATIA 未开启")




# ==========================================
# 主程序：导航容器
# ==========================================
class MainApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Tecnomatix PDPS 综合集成工作站 V13.0")
        self.root.geometry("2400x1600")

        # --- 1. 设定参考字体大小 ---
        base_font_size = 10  # 基础字体
        # 计算适合 2.5 倍缩放的行高，通常 3~4 倍于字体大小比较稳妥
        # 如果 scaling 是 2.5，原本 20 像素的行高现在建议设为 45-50
        calculated_row_height = int(base_font_size * 4.5)

        # --- 2. 样式美化 ---
        style = ttk.Style()
        style.theme_use('clam')

        # 核心修复：设置 Treeview 的行高和字体
        style.configure("Treeview",
                        font=("微软雅黑", base_font_size),
                        rowheight=calculated_row_height)

        # 设置表头字体（表头有时也会挤在一起）
        style.configure("Treeview.Heading",
                        font=("微软雅黑", base_font_size, "bold"))

        # 样式美化
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TNotebook.Tab", font=("微软雅黑", 11), padding=[20, 5])

        # 核心导航：Notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # 实例化页面 - 修复：传入 root 参数
        self.page_manager = ProjectManagerPage(self.notebook, self.root)
        self.page_toolbox = ToolBoxPage(self.notebook)
        self.page_CatiaGun = CatiaGunPlacementPage(self.notebook)

        # 添加到选项卡
        self.notebook.add(self.page_manager, text="  📂 项目路径管理  ")
        self.notebook.add(self.page_toolbox, text="  🛠️ Catia工具箱  ")
        self.notebook.add(self.page_CatiaGun, text="  🔧 插枪工具  ")  # ← 修改标签

        # 状态栏
        self.status = tk.Label(self.root, text="就绪 | 请选择功能模块", bd=1, relief=tk.SUNKEN, anchor=tk.W,
                               font=("微软雅黑", 9))
        self.status.pack(side=tk.BOTTOM, fill=tk.X)



if __name__ == "__main__":
    try:
        # 告诉 Windows 这个程序是 DPI 感知的 (Windows 8.1+)
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            # 备用方案，兼容老版本 Windows (Windows Vista/7)
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    root = tk.Tk()
    # --- 2. 解决 4K 屏幕下界面太小的问题 ---
    # 调整 Tkinter 的全局缩放因子 (DPI/72)
    # 如果你觉得还是太小，可以把 2.0 改成 2.5 或 3.0；如果觉得太大，改成 1.5
    root.tk.call('tk', 'scaling', 2.5)

    # --- 3. 全局接管默认字体 ---
    # 这样你就不用在每个 Label 或 Button 里辛苦地写 font=("微软雅黑", 9) 了
    default_font = tkfont.nametofont("TkDefaultFont")
    default_font.configure(family="微软雅黑", size=10)

    text_font = tkfont.nametofont("TkTextFont")
    text_font.configure(family="微软雅黑", size=10)

    fixed_font = tkfont.nametofont("TkFixedFont")
    fixed_font.configure(family="Consolas", size=10)

    # 强制所有标准 Tkinter 组件使用这个默认字体
    root.option_add("*Font", default_font)

    app = MainApp(root)

    root.mainloop()