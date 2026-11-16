# -*- coding: utf-8 -*-
"""
AlphaExtruderUI.py
PySide2 / PySide6 UI for 3ds Max: Transparent Image Alpha Cutout -> Extrude -> Exact UV Map.
"""

import os
import sys
import json
import importlib
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import AlphaExtruderCore as core
importlib.reload(core)

_CONFIG_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "AlphaExtruder",
    "settings.json",
)

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtGui import QImage, QPixmap, QPainter, QColor, QPen, QPolygonF
    QT_VER = 6
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets
    from PySide2.QtCore import Qt, QPointF
    from PySide2.QtGui import QImage, QPixmap, QPainter, QColor, QPen, QPolygonF
    QT_VER = 2

_DIALOG_INSTANCE = None


def _get_max_main_window():
    try:
        import qtmax
        return qtmax.GetQMaxMainWindow()
    except Exception:
        return None


class DropPreviewLabel(QtWidgets.QLabel):
    fileDropped = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(320, 340)
        self.setText("拖拽透明底 PNG/TGA 图片到此处\n或点击上方“选择图片”")
        self.setStyleSheet(
            "QLabel { background-color: #242629; border: 1px dashed #555; border-radius: 6px; color: #aaa; }"
        )
        self._base_qimage = None
        self._loops = []
        self._outer_count = 0

    def set_preview_data(self, image_path, loops, outer_count):
        img = QImage(image_path)
        if not img.isNull():
            self._base_qimage = img
        self._loops = loops or []
        self._outer_count = outer_count
        self.update_render()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._base_qimage is not None:
            self.update_render()

    def update_render(self):
        if self._base_qimage is None or self._base_qimage.isNull():
            return
        w_box = max(120, self.width() - 8)
        h_box = max(120, self.height() - 8)
        iw = self._base_qimage.width()
        ih = self._base_qimage.height()
        scale = min(w_box / float(iw), h_box / float(ih))
        tw = max(1, int(round(iw * scale)))
        th = max(1, int(round(ih * scale)))

        canvas = QPixmap(tw, th)
        canvas.fill(QColor(38, 40, 44))
        p = QPainter(canvas)
        p.setRenderHint(QPainter.Antialiasing, True)

        # Draw subtle checkerboard background
        tile = 12
        c1 = QColor(52, 54, 58)
        c2 = QColor(40, 42, 46)
        for y in range(0, th, tile):
            for x in range(0, tw, tile):
                p.fillRect(x, y, tile, tile, c1 if ((x // tile + y // tile) & 1) == 0 else c2)

        # Draw source image
        p.drawImage(QtCore.QRect(0, 0, tw, th), self._base_qimage)

        # Draw extracted vector contours
        outer_pen = QPen(QColor(0, 230, 255, 230), 1.6)
        hole_pen = QPen(QColor(255, 90, 200, 230), 1.4)
        for idx, lp in enumerate(self._loops):
            poly = QPolygonF([QPointF(pt[0] * scale, pt[1] * scale) for pt in lp])
            p.setPen(outer_pen if idx < self._outer_count else hole_pen)
            p.drawPolygon(poly)

        p.end()
        self.setPixmap(canvas)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path and os.path.isfile(path):
                self.fileDropped.emit(path)


class AlphaExtruderDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("透明图片一键抠图挤出贴图 (AlphaExtruder)")
        self.setMinimumWidth(780)
        self.setMinimumHeight(560)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)

        self._image_path = ""
        self._side_color = (245, 245, 245)
        self._preview_timer = QtCore.QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(120)
        self._preview_timer.timeout.connect(self._refresh_preview)

        self._build_ui()
        self._apply_dark_style()
        self._load_settings()

    def _apply_dark_style(self):
        self.setStyleSheet("""
            QDialog { background-color: #2c2e33; color: #e6e6e6; font-family: "Microsoft YaHei", sans-serif; font-size: 12px; }
            QGroupBox { border: 1px solid #44474f; border-radius: 5px; margin-top: 10px; padding-top: 10px; font-weight: bold; color: #8ab4f8; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                background-color: #1f2124; border: 1px solid #4a4d55; border-radius: 3px; padding: 3px 6px; color: #eee;
            }
            QPushButton { background-color: #3d4149; border: 1px solid #555963; border-radius: 4px; padding: 5px 12px; color: #eee; }
            QPushButton:hover { background-color: #4a4f59; }
            QPushButton#btnGenerate { background-color: #1f7aec; border: 1px solid #358cf5; font-size: 14px; font-weight: bold; padding: 10px; }
            QPushButton#btnGenerate:hover { background-color: #2b86f7; }
            QCheckBox { spacing: 6px; }
        """)

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(10)

        # Top file bar
        file_row = QtWidgets.QHBoxLayout()
        self.ed_path = QtWidgets.QLineEdit()
        self.ed_path.setPlaceholderText("选择透明底 PNG / TGA / TIFF 图片路径...")
        self.ed_path.editingFinished.connect(self._on_path_edited)
        btn_browse = QtWidgets.QPushButton("选择图片...")
        btn_browse.clicked.connect(self._on_browse)
        file_row.addWidget(QtWidgets.QLabel("图片文件:"))
        file_row.addWidget(self.ed_path, 1)
        file_row.addWidget(btn_browse)
        root.addLayout(file_row)

        # Main split: Left Preview | Right Parameters
        main_row = QtWidgets.QHBoxLayout()

        # Left preview panel
        left_box = QtWidgets.QVBoxLayout()
        self.lbl_preview = DropPreviewLabel()
        self.lbl_preview.fileDropped.connect(self.load_image)
        self.lbl_info = QtWidgets.QLabel("青线 = 外轮廓  |  紫线 = 内部镂空孔洞")
        self.lbl_info.setStyleSheet("color: #9aa0a6; font-size: 11px;")
        left_box.addWidget(self.lbl_preview, 1)
        left_box.addWidget(self.lbl_info)
        main_row.addLayout(left_box, 5)

        # Right parameters panel
        right_box = QtWidgets.QVBoxLayout()

        # Group 1: Contour extraction
        g_cut = QtWidgets.QGroupBox("1. 透明抠图轮廓")
        f_cut = QtWidgets.QFormLayout(g_cut)

        self.sp_thresh = QtWidgets.QSpinBox()
        self.sp_thresh.setRange(1, 254)
        self.sp_thresh.setValue(128)
        self.sp_thresh.valueChanged.connect(self._schedule_preview)
        f_cut.addRow("Alpha 阈值 (1-254):", self.sp_thresh)

        self.sp_offset = QtWidgets.QDoubleSpinBox()
        self.sp_offset.setRange(-30.0, 60.0)
        self.sp_offset.setSingleStep(0.5)
        self.sp_offset.setValue(0.0)
        self.sp_offset.setToolTip("0=严格贴合原图边缘；负数=向内微缩去锯齿；正数=向外扩边(亚克力边框)")
        self.sp_offset.valueChanged.connect(self._schedule_preview)
        f_cut.addRow("轮廓偏移 (px):", self.sp_offset)

        self.sp_simplify = QtWidgets.QDoubleSpinBox()
        self.sp_simplify.setRange(0.1, 8.0)
        self.sp_simplify.setSingleStep(0.1)
        self.sp_simplify.setValue(0.7)
        self.sp_simplify.setToolTip("Douglas-Peucker 简化容差，越小越贴近像素细节，越大节点越精简")
        self.sp_simplify.valueChanged.connect(self._schedule_preview)
        f_cut.addRow("曲线简化容差 (px):", self.sp_simplify)

        self.cb_smooth = QtWidgets.QComboBox()
        self.cb_smooth.addItems(["0 - 精确折线 (推荐，防自交)", "1 - 轻度圆滑 (Chaikin x1)", "2 - 高度圆滑 (Chaikin x2)"])
        self.cb_smooth.currentIndexChanged.connect(self._schedule_preview)
        f_cut.addRow("边缘平滑:", self.cb_smooth)

        self.sp_min_area = QtWidgets.QDoubleSpinBox()
        self.sp_min_area.setRange(1.0, 10000.0)
        self.sp_min_area.setValue(25.0)
        self.sp_min_area.valueChanged.connect(self._schedule_preview)
        f_cut.addRow("过滤噪点面积 (px²):", self.sp_min_area)

        self.chk_holes = QtWidgets.QCheckBox("保留内部镂空孔洞 (如发丝/手臂间隙)")
        self.chk_holes.setChecked(True)
        self.chk_holes.stateChanged.connect(self._schedule_preview)
        f_cut.addRow(self.chk_holes)
        right_box.addWidget(g_cut)

        # Group 2: Extrude & Dimensions
        g_ext = QtWidgets.QGroupBox("2. 挤出与模型尺寸")
        f_ext = QtWidgets.QFormLayout(g_ext)

        self.cb_size_mode = QtWidgets.QComboBox()
        self.cb_size_mode.addItems(["按总高度缩放 (mm)", "按总宽度缩放 (mm)", "按像素比例 (mm/px)"])
        self.sp_size_val = QtWidgets.QDoubleSpinBox()
        self.sp_size_val.setRange(0.01, 100000.0)
        self.sp_size_val.setValue(200.0)
        size_row = QtWidgets.QHBoxLayout()
        size_row.addWidget(self.cb_size_mode, 1)
        size_row.addWidget(self.sp_size_val)
        f_ext.addRow("目标尺寸:", size_row)

        self.sp_depth = QtWidgets.QDoubleSpinBox()
        self.sp_depth.setRange(0.01, 5000.0)
        self.sp_depth.setSingleStep(1.0)
        self.sp_depth.setValue(5.0)
        f_ext.addRow("挤出厚度 (mm):", self.sp_depth)

        self.cb_orient = QtWidgets.QComboBox()
        self.cb_orient.addItems(["立放 (XZ 正面朝前视图)", "平放 (XY 正面朝顶视图)"])
        f_ext.addRow("模型摆放方向:", self.cb_orient)

        self.cb_cap = QtWidgets.QComboBox()
        self.cb_cap.addItems(["Morph 平面封口 (简洁)", "Grid 网格封口 (适合后续弯曲)"])
        f_ext.addRow("封口类型:", self.cb_cap)

        self.chk_poly = QtWidgets.QCheckBox("生成后塌陷为 Editable Poly (默认保留修改器栈)")
        self.chk_poly.setChecked(False)
        f_ext.addRow(self.chk_poly)
        right_box.addWidget(g_ext)

        # Group 3: Material & UV
        g_mat = QtWidgets.QGroupBox("3. 严丝合缝贴图与材质")
        f_mat = QtWidgets.QFormLayout(g_mat)

        self.chk_dilate = QtWidgets.QCheckBox("边缘防白边扩色 (自动生成无白边贴图，强烈推荐)")
        self.chk_dilate.setChecked(True)
        f_mat.addRow(self.chk_dilate)

        self.cb_mat_mode = QtWidgets.QComboBox()
        self.cb_mat_mode.addItems([
            "整体全包贴图 (正反+侧面延续边缘色)",
            "正反贴图 + 侧面纯色",
            "正反贴图 + 侧面透明亚克力",
            "仅正面贴图 + 背面/侧面纯色",
        ])
        f_mat.addRow("贴图覆盖模式:", self.cb_mat_mode)

        self.btn_side_color = QtWidgets.QPushButton("选择侧面/背面颜色")
        self._update_color_btn()
        self.btn_side_color.clicked.connect(self._on_pick_color)
        f_mat.addRow("侧面/背面纯色:", self.btn_side_color)

        self.cb_mat_type = QtWidgets.QComboBox()
        self.cb_mat_type.addItems(["自动适配当前渲染器 (推荐)", "VRayMtl (V-Ray 专用)", "PhysicalMaterial (Arnold/通用)", "Standard (传统标准)"])
        f_mat.addRow("材质类型:", self.cb_mat_type)

        self.ed_mat_name = QtWidgets.QLineEdit()
        self.ed_mat_name.setPlaceholderText("留空则直接使用图片文件名 (自动去重)")
        f_mat.addRow("材质名称:", self.ed_mat_name)
        right_box.addWidget(g_mat)

        right_box.addStretch(1)

        self.btn_gen = QtWidgets.QPushButton("一键抠图挤出并贴图")
        self.btn_gen.setObjectName("btnGenerate")
        self.btn_gen.clicked.connect(self._on_generate)
        right_box.addWidget(self.btn_gen)

        main_row.addLayout(right_box, 4)
        root.addLayout(main_row, 1)

        self.lbl_status = QtWidgets.QLabel("就绪：请选择一张透明底图片。")
        self.lbl_status.setStyleSheet("color: #bbb; padding: 2px;")
        root.addWidget(self.lbl_status)

    def _update_color_btn(self):
        r, g, b = self._side_color
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        fg = "#111" if lum > 140 else "#eee"
        self.btn_side_color.setStyleSheet(
            f"background-color: rgb({r},{g},{b}); color: {fg}; border: 1px solid #666;"
        )
        self.btn_side_color.setText(f"RGB ({r}, {g}, {b})")

    def _on_pick_color(self):
        r, g, b = self._side_color
        col = QtWidgets.QColorDialog.getColor(QColor(r, g, b), self, "选择侧面/背面纯色")
        if col.isValid():
            self._side_color = (col.red(), col.green(), col.blue())
            self._update_color_btn()

    def _on_browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择带透明通道的图片", "",
            "图片文件 (*.png *.tga *.tif *.tiff *.webp *.bmp);;所有文件 (*.*)"
        )
        if path:
            self.load_image(path)

    def _on_path_edited(self):
        path = self.ed_path.text().strip().strip('"')
        if path and os.path.isfile(path) and path != self._image_path:
            self.load_image(path)

    def load_image(self, path):
        self._image_path = path
        self.ed_path.setText(path)
        stem = os.path.splitext(os.path.basename(path))[0]
        self.ed_mat_name.setPlaceholderText(f"默认使用图片名: {stem}")
        self._refresh_preview()

    def _schedule_preview(self):
        if self._image_path and os.path.isfile(self._image_path):
            self._preview_timer.start()

    def _refresh_preview(self):
        if not self._image_path or not os.path.isfile(self._image_path):
            return
        try:
            self.lbl_status.setText("正在分析图片轮廓...")
            QtWidgets.QApplication.processEvents()
            res = core.process_image_to_contours(
                image_path=self._image_path,
                alpha_threshold=self.sp_thresh.value(),
                contour_offset_px=self.sp_offset.value(),
                simplify_eps=self.sp_simplify.value(),
                smooth_iters=self.cb_smooth.currentIndex(),
                min_island_area=self.sp_min_area.value(),
                min_hole_area=self.sp_min_area.value(),
                keep_holes=self.chk_holes.isChecked(),
                dilate_edges=False,
            )
            self.lbl_preview.set_preview_data(self._image_path, res["loops"], res["outer_count"])
            self.lbl_info.setText(
                f"尺寸: {res['width']}×{res['height']} px  |  外轮廓: {res['outer_count']}  |  "
                f"孔洞: {res['hole_count']}  |  总节点: {res['total_knots']}  |  耗时: {res['elapsed_ms']:.0f} ms"
            )
            self.lbl_status.setText("轮廓预览已更新，可点击下方按钮直接生成 3D 模型。")
        except Exception as e:
            self.lbl_status.setText(f"预览失败: {e}")

    def _load_settings(self):
        try:
            if os.path.isfile(_CONFIG_PATH):
                with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if "mat_type" in cfg:
                    self.cb_mat_type.setCurrentIndex(int(cfg["mat_type"]))
                if "mat_mode" in cfg:
                    self.cb_mat_mode.setCurrentIndex(int(cfg["mat_mode"]))
                if "depth" in cfg:
                    self.sp_depth.setValue(float(cfg["depth"]))
                if "size_val" in cfg:
                    self.sp_size_val.setValue(float(cfg["size_val"]))
                if "size_mode" in cfg:
                    self.cb_size_mode.setCurrentIndex(int(cfg["size_mode"]))
                if "orient" in cfg:
                    self.cb_orient.setCurrentIndex(int(cfg["orient"]))
        except Exception:
            pass

    def _save_settings(self):
        try:
            os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
            cfg = {
                "mat_type": self.cb_mat_type.currentIndex(),
                "mat_mode": self.cb_mat_mode.currentIndex(),
                "depth": self.sp_depth.value(),
                "size_val": self.sp_size_val.value(),
                "size_mode": self.cb_size_mode.currentIndex(),
                "orient": self.cb_orient.currentIndex(),
            }
            with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _on_generate(self):
        path = self.ed_path.text().strip().strip('"')
        if not path or not os.path.isfile(path):
            QtWidgets.QMessageBox.warning(self, "提示", "请先选择有效的透明图片文件！")
            return

        size_modes = ["height", "width", "pixel"]
        mat_modes = ["full", "side_solid", "side_acrylic", "front_only"]
        mat_types = ["auto", "vray", "physical", "standard"]
        orients = ["standing", "flat"]
        self._save_settings()

        try:
            self.btn_gen.setEnabled(False)
            self.lbl_status.setText("正在生成抠图挤出模型并匹配 UV 贴图...")
            QtWidgets.QApplication.processEvents()

            res = core.build_extruded_cutout_in_max(
                image_path=path,
                material_name=self.ed_mat_name.text().strip() or None,
                alpha_threshold=self.sp_thresh.value(),
                contour_offset_px=self.sp_offset.value(),
                simplify_eps=self.sp_simplify.value(),
                smooth_iters=self.cb_smooth.currentIndex(),
                min_island_area=self.sp_min_area.value(),
                min_hole_area=self.sp_min_area.value(),
                keep_holes=self.chk_holes.isChecked(),
                dilate_edges=self.chk_dilate.isChecked(),
                size_mode=size_modes[self.cb_size_mode.currentIndex()],
                target_size=self.sp_size_val.value(),
                extrude_depth=self.sp_depth.value(),
                cap_type=self.cb_cap.currentIndex(),
                orientation=orients[self.cb_orient.currentIndex()],
                material_mode=mat_modes[self.cb_mat_mode.currentIndex()],
                side_color=self._side_color,
                mat_type=mat_types[self.cb_mat_type.currentIndex()],
                collapse_poly=self.chk_poly.isChecked(),
            )
            self.lbl_status.setText(
                f"生成成功！对象 [{res['node_name']}] | 材质 [{res['material_name']}] | "
                f"外轮廓 {res['outer_count']} / 孔洞 {res['hole_count']} | 尺寸 {res['canvas_width']:.1f}×{res['canvas_height']:.1f} mm"
            )
        except Exception as e:
            traceback.print_exc()
            QtWidgets.QMessageBox.critical(self, "生成出错", f"{e}\n\n{traceback.format_exc()}")
            self.lbl_status.setText(f"生成出错: {e}")
        finally:
            self.btn_gen.setEnabled(True)


def show_dialog(initial_image=None):
    global _DIALOG_INSTANCE
    if _DIALOG_INSTANCE is not None:
        try:
            _DIALOG_INSTANCE.close()
            _DIALOG_INSTANCE.deleteLater()
        except Exception:
            pass
    _DIALOG_INSTANCE = AlphaExtruderDialog(parent=_get_max_main_window())
    if initial_image and os.path.isfile(initial_image):
        _DIALOG_INSTANCE.load_image(initial_image)
    _DIALOG_INSTANCE.show()
    _DIALOG_INSTANCE.raise_()
    _DIALOG_INSTANCE.activateWindow()
    return _DIALOG_INSTANCE


if __name__ == "__main__":
    show_dialog()
