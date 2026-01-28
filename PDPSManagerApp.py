import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import pandas as pd
import zipfile
import winreg
import re
from PIL import Image, ImageTk

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

    def extract_weld_points(self) -> List[Dict]:
        """提取焊点数据（兼容两种连接方式）"""
        if not self.catia_app:
            self.status_message = "请先连接 CATIA"
            return []

        self.weld_points_data = []  # 清空之前的数据

        try:
            # 根据连接类型获取活动文档
            if self.connection_type == 'pycatia':
                doc = self.catia_app.active_document()
                if not doc:
                    self.status_message = "没有活动的 CATIA 文档"
                    return []
                if not hasattr(doc, 'part'):
                    self.status_message = "当前文档不是 Part 文档 (.CATPart)"
                    return []
                part = doc.part()
                selection = doc.selection()
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
                        x, y, z = coords
                        p_type = "焊点" if ("Weld" in name or "Spot" in name) else "普通点"
                        class_val = "PmWeldPoint"
                        ext_id = name
                        pt_name = name
                        location_str = f"{x:.3f},{y:.3f},{z:.3f}"
                        x_str = f"{x:.3f}"
                        y_str = f"{y:.3f}"
                        z_str = f"{z:.3f}"
                        type_str = p_type
                        self.weld_points_data.append({
                            "Class": class_val,
                            "ExternalId": ext_id,
                            "Name": pt_name,
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

    def setup_ui(self):
        # 标题栏
        title_frame = tk.Frame(self, bg="#34495e", pady=10)
        title_frame.pack(fill=tk.X)

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
                  bg="#27ae60", fg="white", width=15, height=2).pack(side=tk.LEFT, padx=(0,10))
        tk.Button(btn_frame1, text="📥 导入数据", command=self.import_weld_data,
                  bg="#9b59b6", fg="white", width=15, height=2).pack(side=tk.LEFT, padx=(0, 10))
        tk.Button(btn_frame1, text="💾 生成点球", command=self.newball,
                  bg="#27ae60", fg="white", width=15, height=2).pack(side=tk.LEFT)

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
        """连接 CATIA"""
        if not PYCATIA_AVAILABLE:
            messagebox.showerror("错误", "pycatia 库未安装！\n\n请先运行: pip install pycatia")
            return

        try:
            self.catia_app = catia()

            # 设置到业务逻辑类
            self.weld_extractor.set_catia_app(self.catia_app)
            self.sphere_scanner.set_catia_app(self.catia_app)
            import win32com.client
            self.catia_app = win32com.client.Dispatch("CATIA.Application")

            # 创建或更新 CATIAWeldPointExtractor 实例
            if not hasattr(self, 'weld_extractor'):
                self.weld_extractor = CATIAWeldPointExtractor()

            # 传递 CATIA 应用实例给提取器
            self.weld_extractor.set_catia_app(self.catia_app, 'win32com')

            self.status_label.config(text="已连接 | CATIA (pycatia)")
            messagebox.showinfo("成功", "CATIA 连接成功（使用 pycatia）")
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

            # 提取焊点数据
            weld_data = self.weld_extractor.extract_weld_points()

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
        self._export_data(weld_points_data, "焊点数据.xlsx")

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


class CatiaGun(tk.Frame):
    pass

# ==========================================
# 主程序：导航容器
# ==========================================
class MainApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Tecnomatix PDPS 综合集成工作站 V13.0")
        self.root.geometry("1300x900")

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
        self.page_CatiaGun = CatiaGun(self.notebook)

        # 添加到选项卡
        self.notebook.add(self.page_manager, text="  📂 项目路径管理  ")
        self.notebook.add(self.page_toolbox, text="  🛠️ Catia工具箱  ")
        self.notebook.add(self.page_CatiaGun, text="插枪工具")

        # 状态栏
        self.status = tk.Label(self.root, text="就绪 | 请选择功能模块", bd=1, relief=tk.SUNKEN, anchor=tk.W,
                               font=("微软雅黑", 9))
        self.status.pack(side=tk.BOTTOM, fill=tk.X)


if __name__ == "__main__":
    root = tk.Tk()
    app = MainApp(root)
    root.mainloop()