# -*- coding: utf-8 -*-
"""CatiaGunPlacer —— 焊枪批量放置 + 库卡 TCP / 欧拉角矩阵变换。"""

import os
import math
import time
from datetime import datetime

import numpy as np


class CatiaGunPlacer:
    def __init__(self, catia_app=None):
        self.catia_app = catia_app
        self.connection_type = None
        self.status_message = "就绪"
        self.gun_template_path = ""
        self.rotation_order = 'kuka'

    def set_rotation_order(self, order: str):
        valid_orders = ['kuka', 'catia_zyx', 'catia_xyz']
        if order in valid_orders:
            self.rotation_order = order

    def set_catia_app(self, catia_app, connection_type='win32com'):
        self.catia_app = catia_app
        self.connection_type = connection_type
        self.status_message = f"CATIA 已连接 ({connection_type})"

    def is_catia_connected(self) -> bool:
        if not self.catia_app:
            return False
        try:
            if self.connection_type == 'pycatia':
                _ = self.catia_app.documents.count
            else:
                _ = self.catia_app.Documents.Count
            return True
        except Exception:
            return False

    def has_active_product(self) -> bool:
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
                except Exception:
                    return False
        except Exception:
            return False

    # ── 数学：欧拉角 / 矩阵 ───────────────────────────────────────────────
    def euler_to_rotation_matrix(self, rx, ry, rz, order=None):
        if order is None:
            order = self.rotation_order
        rx_r, ry_r, rz_r = math.radians(rx), math.radians(ry), math.radians(rz)
        Rx = np.array([[1, 0, 0], [0, math.cos(rx_r), -math.sin(rx_r)], [0, math.sin(rx_r), math.cos(rx_r)]])
        Ry = np.array([[math.cos(ry_r), 0, math.sin(ry_r)], [0, 1, 0], [-math.sin(ry_r), 0, math.cos(ry_r)]])
        Rz = np.array([[math.cos(rz_r), -math.sin(rz_r), 0], [math.sin(rz_r), math.cos(rz_r), 0], [0, 0, 1]])
        if order in ('kuka', 'catia_zyx'):
            return Rz @ Ry @ Rx
        else:
            return Rx @ Ry @ Rz

    def euler_to_catia_components(self, x, y, z, rx, ry, rz):
        R = self.euler_to_rotation_matrix(rx, ry, rz)
        return [R[0, 0], R[1, 0], R[2, 0], R[0, 1], R[1, 1], R[2, 1], R[0, 2], R[1, 2], R[2, 2], x, y, z]

    def pose_to_matrix(self, x, y, z, rx, ry, rz, order=None):
        """将 6D 位姿（XYZ + 欧拉角）转换为 4x4 齐次变换矩阵。"""
        R = self.euler_to_rotation_matrix(rx, ry, rz, order)
        M = np.eye(4)
        M[:3, :3] = R
        M[0, 3] = x
        M[1, 3] = y
        M[2, 3] = z
        return M

    def get_kuka_correction_matrix(self, kuka_data):
        """将库卡控制柜 TCP (X, Y, Z, A, B, C) 映射为 CATIA 修正矩阵。"""
        try:
            kx = float(kuka_data.get('X', 0))
            ky = float(kuka_data.get('Y', 0))
            kz = float(kuka_data.get('Z', 0))
            ka = float(kuka_data.get('A', 0))
            kb = float(kuka_data.get('B', 0))
            kc = float(kuka_data.get('C', 0))

            # 根据实测数据的映射规律 (补偿模板的建模朝向)
            cx = -kz
            cy = -ky
            cz = kx
            crx = kc
            cry = -kb
            crz = ka

            return self.pose_to_matrix(cx, cy, cz, crx, cry, crz)
        except Exception as e:
            print(f"TCP 转换失败: {e}")
            return np.eye(4)

    # ── 批量放置 ──────────────────────────────────────────────────────────
    def place_guns_batch(self, gun_template, gun_data, progress_callback=None,
                         correction_values=None, rotation_order=None, kuka_tcp_input=None):
        """批量放置焊枪，支持库卡 TCP 矩阵变换与普通坐标偏移。"""
        if not self.is_catia_connected():
            return 0, 0, ["✗ CATIA 未连接"]
        if not self.has_active_product():
            return 0, 0, ["✗ 请先打开一个 Product 文档"]

        if rotation_order:
            self.set_rotation_order(rotation_order)

        gun_template_normalized = os.path.normpath(gun_template).replace('/', '\\')
        success_count, failed_count = 0, 0
        status_list = []

        # 1. 预计算修正矩阵
        M_corr = np.eye(4)
        if kuka_tcp_input:
            M_corr = self.get_kuka_correction_matrix(kuka_tcp_input)

        try:
            # 2. CATIA 环境与容器准备
            if self.connection_type == 'pycatia':
                doc = self.catia_app.active_document
                product = doc.product
                com_products = product.products.com_object
            else:
                doc = self.catia_app.ActiveDocument
                product = doc.Product
                com_products = product.Products

            try:
                product_name = product.name if self.connection_type == 'pycatia' else product.Name
            except Exception:
                product_name = 'Product'

            container_name = f"{product_name}-GUN-{datetime.now().strftime('%Y%m%d_%S')}"

            try:
                container = com_products.AddNewComponent("Product", container_name)
                container_products = container.Products
            except Exception as e:
                print(f"创建容器失败: {e}，将直接在根节点下插入")
                container_products = com_products

            # 3. 循环插入焊枪并应用变换
            total = len(gun_data)
            for idx, gun in enumerate(gun_data):
                try:
                    x = float(gun.get('X', 0))
                    y = float(gun.get('Y', 0))
                    z = float(gun.get('Z', 0))
                    rx = float(gun.get('Rx', 0))
                    ry = float(gun.get('Ry', 0))
                    rz = float(gun.get('Rz', 0))

                    # 没勾选 TCP 且存在普通偏移，直接加到基础坐标上
                    if correction_values and not kuka_tcp_input:
                        x += float(correction_values.get('X', 0))
                        y += float(correction_values.get('Y', 0))
                        z += float(correction_values.get('Z', 0))
                        rx += float(correction_values.get('Rx', 0))
                        ry += float(correction_values.get('Ry', 0))
                        rz += float(correction_values.get('Rz', 0))

                    M_pose = self.pose_to_matrix(x, y, z, rx, ry, rz)
                    # 核心修正：矩阵乘法 M_final = M_pose * M_corr
                    M_final = M_pose @ M_corr

                    # 插入模型文件
                    file_array = (gun_template_normalized,)
                    container_products.AddComponentsFromFiles(file_array, "All")
                    time.sleep(0.3)

                    # 获取刚刚插入的对象并重命名
                    count = container_products.Count
                    new_product = container_products.Item(count)
                    new_product.Name = gun.get('Name', f'Gun_{idx + 1}')

                    # 提取 CATIA 需要的 12 个分量
                    components = [
                        M_final[0, 0], M_final[1, 0], M_final[2, 0],
                        M_final[0, 1], M_final[1, 1], M_final[2, 1],
                        M_final[0, 2], M_final[1, 2], M_final[2, 2],
                        M_final[0, 3], M_final[1, 3], M_final[2, 3]
                    ]
                    new_product.Position.SetComponents(components)

                    success_count += 1
                    status_list.append("✓ 成功")
                    if progress_callback:
                        progress_callback(idx + 1, total, f"插入: {new_product.Name}")

                except Exception as e:
                    failed_count += 1
                    status_list.append(f"✗ {str(e)[:30]}")
                    print(f"插入第{idx + 1}个失败: {e}")

            # 4. 更新文档
            try:
                if self.connection_type == 'pycatia':
                    doc.product.update()
                else:
                    doc.Product.Update()
            except Exception:
                pass

            self.status_message = f"完成 | 成功: {success_count} | 失败: {failed_count}"
            return success_count, failed_count, status_list

        except Exception as e:
            return success_count, failed_count, [f"✗ 致命错误: {str(e)}"]
