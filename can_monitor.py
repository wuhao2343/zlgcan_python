'''
    交互式CAN通道控制台 (GUI版 - customtkinter)
    支持选择打开CAN0/CAN1/同时打开两个通道
    支持在已打开的通道上发送数据，同时显示接收到的数据
    报文显示区和操作区完全独立，互不干扰
'''

from zlgcan import *
import threading
import time
import sys
import customtkinter as ctk
from tkinter import messagebox
from collections import deque

# ========== 配置区 ==========
BAUD_RATE = "500000"    # 默认波特率
MAX_LOG_LINES = 2000    # 报文区最大显示行数
BAUD_RATE_OPTIONS = [
    "10000", "20000", "50000", "100000", "125000",
    "250000", "500000", "800000", "1000000"
]
# ============================

# 全局状态
thread_flag = True
device_handle = None
zcanlib = None
chn_handles = {}        # {通道号: 句柄}
rx_count = {}           # {通道号: 接收计数}
tx_count = {}           # {通道号: 发送计数}
send_history = []       # 发送历史


# ========== CAN 核心功能 ==========

def start_channel(chn):
    """启动指定CAN通道"""
    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/baud_rate", BAUD_RATE.encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        return None

    chn_init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
    chn_init_cfg.can_type = ZCAN_TYPE_CAN
    chn_init_cfg.config.can.mode = 0
    chn_init_cfg.config.can.acc_code = 0
    chn_init_cfg.config.can.acc_mask = 0xFFFFFFFF

    chn_handle = zcanlib.InitCAN(device_handle, chn, chn_init_cfg)
    if chn_handle is None:
        return None

    ret = zcanlib.StartCAN(chn_handle)
    if ret != ZCAN_STATUS_OK:
        return None

    return chn_handle


def send_can_message(chn_handle, can_id, data_bytes, is_extended=False):
    """发送一条CAN数据帧"""
    msgs = (ZCAN_Transmit_Data * 1)()
    memset(addressof(msgs), 0, sizeof(msgs))

    msgs[0].transmit_type = 0
    msgs[0].frame.can_id = can_id & 0x1FFFFFFF
    if is_extended:
        msgs[0].frame.can_id |= (1 << 31)

    msgs[0].frame.can_dlc = len(data_bytes)
    for i, b in enumerate(data_bytes):
        msgs[0].frame.data[i] = b

    ret = zcanlib.Transmit(chn_handle, msgs, 1)
    return ret == 1


def cleanup():
    """清理资源"""
    global thread_flag
    thread_flag = False
    time.sleep(0.05)

    for chn, handle in chn_handles.items():
        if handle is not None:
            zcanlib.ResetCAN(handle)

    if device_handle is not None:
        zcanlib.CloseDevice(device_handle)


# ========== GUI 应用 ==========

class CanMonitorApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("ZLG CAN Monitor")
        self.geometry("1000x650")
        self.minsize(800, 500)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.msg_queue = deque(maxlen=MAX_LOG_LINES)
        self.auto_scroll = True
        self.line_count = 0
        self.device_connected = False

        self._build_ui()

        # 定时刷新报文（即使未连接也启动，后续有数据才显示）
        self._poll_messages()

        # 窗口关闭事件
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        """构建界面"""
        # ===== 设备连接面板 =====
        self.conn_frame = ctk.CTkFrame(self, height=40, corner_radius=0)
        self.conn_frame.pack(fill="x", padx=0, pady=0)
        self.conn_frame.pack_propagate(False)

        # 设备状态指示
        self.device_indicator = ctk.CTkLabel(
            self.conn_frame,
            text="  ● 设备未连接",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#F44336"
        )
        self.device_indicator.pack(side="left", padx=10)

        # 设备类型
        ctk.CTkLabel(
            self.conn_frame,
            text="  USBCAN-II",
            font=ctk.CTkFont(size=12),
            text_color="#888888"
        ).pack(side="left")

        # 波特率选择
        ctk.CTkLabel(
            self.conn_frame,
            text="  波特率:",
            font=ctk.CTkFont(size=12),
            text_color="#AAAAAA"
        ).pack(side="left", padx=(15, 5))

        self.baud_combo = ctk.CTkComboBox(
            self.conn_frame, width=100,
            values=[f"{int(b)//1000}K" if int(b) >= 1000 else b for b in BAUD_RATE_OPTIONS],
            font=ctk.CTkFont(size=12),
            state="readonly"
        )
        default_idx = BAUD_RATE_OPTIONS.index(BAUD_RATE) if BAUD_RATE in BAUD_RATE_OPTIONS else 13
        self.baud_combo.set(f"{int(BAUD_RATE_OPTIONS[default_idx])//1000}K" if int(BAUD_RATE_OPTIONS[default_idx]) >= 1000 else BAUD_RATE_OPTIONS[default_idx])
        self.baud_combo.pack(side="left", padx=(0, 10))

        # 连接/断开按钮
        self.connect_btn = ctk.CTkButton(
            self.conn_frame, text="打开设备", width=90, height=28,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#4CAF50", hover_color="#388E3C",
            command=self._do_connect
        )
        self.connect_btn.pack(side="right", padx=10)

        # ===== 通道启停面板 =====
        self.channel_frame = ctk.CTkFrame(self, height=36, corner_radius=0, fg_color="#1E1E1E")
        self.channel_frame.pack_propagate(False)
        # 初始隐藏，设备连接后才显示

        ctk.CTkLabel(
            self.channel_frame,
            text="  通道控制:",
            font=ctk.CTkFont(size=12),
            text_color="#AAAAAA"
        ).pack(side="left", padx=5)

        self.chn0_start_btn = ctk.CTkButton(
            self.channel_frame, text="启动 CAN0", width=90, height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#4CAF50", hover_color="#388E3C",
            command=lambda: self._do_start_channel(0)
        )
        self.chn0_start_btn.pack(side="left", padx=3)

        self.chn0_status_label = ctk.CTkLabel(
            self.channel_frame, text="未启动", font=ctk.CTkFont(size=11), text_color="#888888"
        )
        self.chn0_status_label.pack(side="left", padx=(0, 10))

        self.chn1_start_btn = ctk.CTkButton(
            self.channel_frame, text="启动 CAN1", width=90, height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#4CAF50", hover_color="#388E3C",
            command=lambda: self._do_start_channel(1)
        )
        self.chn1_start_btn.pack(side="left", padx=3)

        self.chn1_status_label = ctk.CTkLabel(
            self.channel_frame, text="未启动", font=ctk.CTkFont(size=11), text_color="#888888"
        )
        self.chn1_status_label.pack(side="left", padx=(0, 10))

        self.chn_all_start_btn = ctk.CTkButton(
            self.channel_frame, text="启动全部", width=80, height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#2196F3", hover_color="#1976D2",
            command=self._do_start_all_channels
        )
        self.chn_all_start_btn.pack(side="left", padx=3)

        self.chn_all_stop_btn = ctk.CTkButton(
            self.channel_frame, text="停止全部", width=80, height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#F44336", hover_color="#D32F2F",
            command=self._do_stop_all_channels
        )
        self.chn_all_stop_btn.pack(side="left", padx=3)

        # ===== 顶部状态栏 =====
        self.status_frame = ctk.CTkFrame(self, height=32, corner_radius=0)
        self.status_frame.pack(fill="x", padx=0, pady=0)
        self.status_frame.pack_propagate(False)

        self.channel_labels = {}

        # 收发计数（右侧）
        self.stats_label = ctk.CTkLabel(
            self.status_frame,
            text="等待设备连接...",
            font=ctk.CTkFont(size=12, family="Consolas"),
            text_color="#888888"
        )
        self.stats_label.pack(side="right", padx=15)

        # ===== 中间报文显示区 =====
        self.msg_frame = ctk.CTkFrame(self, corner_radius=5)
        self.msg_frame.pack(fill="both", expand=True, padx=8, pady=(5, 3))

        # 报文标题栏
        header_frame = ctk.CTkFrame(self.msg_frame, height=28, corner_radius=0, fg_color="#2B2B2B")
        header_frame.pack(fill="x", padx=2, pady=(2, 0))
        header_frame.pack_propagate(False)

        # 列宽与 _append_message 中一致（使用英文缩写避免中文对齐问题）
        header_text = f"{'Timestamp':<12}{'Dir':<4}{'Chan':<6}{'ID':<12}{'Type':<8}{'DLC':<4}{'Data'}"
        ctk.CTkLabel(
            header_frame,
            text=header_text,
            font=ctk.CTkFont(size=12, family="Consolas"),
            text_color="#888888",
            anchor="w"
        ).pack(side="left", padx=8, pady=2)

        # 清空按钮
        clear_btn = ctk.CTkButton(
            header_frame, text="清空", width=50, height=22,
            font=ctk.CTkFont(size=11),
            command=self._clear_messages
        )
        clear_btn.pack(side="right", padx=5, pady=2)

        # 自动滚动开关
        self.scroll_var = ctk.BooleanVar(value=True)
        scroll_cb = ctk.CTkCheckBox(
            header_frame, text="自动滚动", variable=self.scroll_var,
            font=ctk.CTkFont(size=11), height=22, checkbox_width=16, checkbox_height=16,
            command=self._toggle_scroll
        )
        scroll_cb.pack(side="right", padx=5, pady=2)

        # 报文文本框
        self.msg_text = ctk.CTkTextbox(
            self.msg_frame,
            font=ctk.CTkFont(size=12, family="Consolas"),
            wrap="none",
            state="disabled",
            activate_scrollbars=True
        )
        self.msg_text.pack(fill="both", expand=True, padx=2, pady=(0, 2))

        # 配置文本标签颜色
        self.msg_text._textbox.tag_configure("rx", foreground="#4CAF50")
        self.msg_text._textbox.tag_configure("tx", foreground="#FF9800")
        self.msg_text._textbox.tag_configure("sys", foreground="#2196F3")
        self.msg_text._textbox.tag_configure("data", foreground="#80DEEA")
        self.msg_text._textbox.tag_configure("dim", foreground="#666666")

        # ===== 底部操作区 =====
        self.control_frame = ctk.CTkFrame(self, height=130, corner_radius=5)
        self.control_frame.pack(fill="x", padx=8, pady=(3, 8))
        self.control_frame.pack_propagate(False)

        # --- 发送面板 ---
        send_frame = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        send_frame.pack(fill="x", padx=10, pady=5)

        # 第一行：通道 + ID + 扩展帧 + 远程帧
        row1 = ctk.CTkFrame(send_frame, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 5))

        ctk.CTkLabel(row1, text="通道:", font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 5))
        self.chn_combo = ctk.CTkComboBox(
            row1, width=80,
            values=["--"],
            font=ctk.CTkFont(size=12),
            state="readonly"
        )
        self.chn_combo.set("--")
        self.chn_combo.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(row1, text="ID (hex):", font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 5))
        self.id_entry = ctk.CTkEntry(row1, width=100, font=ctk.CTkFont(size=12, family="Consolas"), placeholder_text="如 1A3")
        self.id_entry.pack(side="left", padx=(0, 15))

        self.ext_var = ctk.BooleanVar(value=False)
        self.ext_cb = ctk.CTkCheckBox(
            row1, text="扩展帧", variable=self.ext_var,
            font=ctk.CTkFont(size=12), height=24, checkbox_width=18, checkbox_height=18
        )
        self.ext_cb.pack(side="left", padx=(0, 10))

        # DLC
        ctk.CTkLabel(row1, text="DLC:", font=ctk.CTkFont(size=12)).pack(side="left", padx=(15, 5))
        self.dlc_label = ctk.CTkLabel(row1, text="0", font=ctk.CTkFont(size=12, family="Consolas"), width=20)
        self.dlc_label.pack(side="left")

        # 第二行：数据 + 发送按钮
        row2 = ctk.CTkFrame(send_frame, fg_color="transparent")
        row2.pack(fill="x", pady=(0, 5))

        ctk.CTkLabel(row2, text="数据 (hex):", font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 5))
        self.data_entry = ctk.CTkEntry(
            row2, width=300,
            font=ctk.CTkFont(size=12, family="Consolas"),
            placeholder_text="空格分隔，如 01 02 FF 00 A5 (最多8字节)"
        )
        self.data_entry.pack(side="left", padx=(0, 15))
        self.data_entry.bind("<KeyRelease>", self._update_dlc)
        self.data_entry.bind("<Return>", lambda e: self._do_send())

        self.send_btn = ctk.CTkButton(
            row2, text="发 送", width=80, height=30,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#4CAF50", hover_color="#388E3C",
            command=self._do_send
        )
        self.send_btn.pack(side="left", padx=(0, 5))

        # 第三行：发送历史 + 重发
        row3 = ctk.CTkFrame(send_frame, fg_color="transparent")
        row3.pack(fill="x")

        ctk.CTkLabel(row3, text="历史:", font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 5))
        self.history_combo = ctk.CTkComboBox(
            row3, width=350,
            values=["(无历史记录)"],
            font=ctk.CTkFont(size=11, family="Consolas"),
            state="readonly"
        )
        self.history_combo.set("(无历史记录)")
        self.history_combo.pack(side="left", padx=(0, 10))

        self.resend_btn = ctk.CTkButton(
            row3, text="重发", width=60, height=26,
            font=ctk.CTkFont(size=12),
            command=self._do_resend
        )
        self.resend_btn.pack(side="left", padx=(0, 10))

        self.fill_btn = ctk.CTkButton(
            row3, text="填入", width=60, height=26,
            font=ctk.CTkFont(size=12),
            fg_color="#555555", hover_color="#666666",
            command=self._fill_from_history
        )
        self.fill_btn.pack(side="left", padx=(0, 10))

        # 重置计数按钮
        self.reset_btn = ctk.CTkButton(
            row3, text="重置计数", width=70, height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#555555", hover_color="#666666",
            command=self._reset_counts
        )
        self.reset_btn.pack(side="right", padx=5)

    def _start_receive_threads(self):
        """启动接收线程"""
        for chn, handle in chn_handles.items():
            t = threading.Thread(target=self._receive_loop, args=(chn, handle), daemon=True)
            t.start()

    # ========== 设备连接与通道管理 ==========

    def _get_baud_value(self):
        """从下拉框文本还原为实际波特率数值字符串"""
        text = self.baud_combo.get()
        display_values = [f"{int(b)//1000}K" if int(b) >= 1000 else b for b in BAUD_RATE_OPTIONS]
        try:
            idx = display_values.index(text)
            return BAUD_RATE_OPTIONS[idx]
        except ValueError:
            if text.upper().endswith("K"):
                return str(int(text[:-1]) * 1000)
            return text

    def _do_connect(self):
        """打开设备"""
        global device_handle, BAUD_RATE
        BAUD_RATE = self._get_baud_value()
        device_handle = zcanlib.OpenDevice(ZCAN_USBCAN2, 0, 0)
        if device_handle == INVALID_DEVICE_HANDLE:
            messagebox.showerror("错误", "打开设备失败！\n请检查设备连接和驱动。")
            return

        self.device_connected = True
        self.device_indicator.configure(text="  ● 设备已连接", text_color="#4CAF50")
        self.connect_btn.configure(text="关闭设备", fg_color="#F44336", hover_color="#D32F2F",
                                   command=self._do_disconnect)
        self.channel_frame.pack(fill="x", padx=0, pady=0, before=self.status_frame)
        self.stats_label.configure(text="请启动通道...")
        self._add_sys_message(f"设备已连接，波特率: {BAUD_RATE} bps")

    def _do_disconnect(self):
        """关闭设备"""
        self._do_stop_all_channels()
        global device_handle
        if device_handle is not None:
            zcanlib.CloseDevice(device_handle)
            device_handle = None

        self.device_connected = False
        self.device_indicator.configure(text="  ● 设备未连接", text_color="#F44336")
        self.connect_btn.configure(text="打开设备", fg_color="#4CAF50", hover_color="#388E3C",
                                   command=self._do_connect)
        self.channel_frame.pack_forget()
        self.stats_label.configure(text="等待设备连接...")
        self._update_chn_combo()
        self._update_status_bar_clear()
        self._add_sys_message("设备已关闭")

    def _do_start_channel(self, chn):
        """启动指定CAN通道"""
        global BAUD_RATE
        if chn in chn_handles:
            messagebox.showwarning("提示", f"CAN{chn} 已经启动")
            return
        if not self.device_connected:
            messagebox.showerror("错误", "请先打开设备")
            return

        BAUD_RATE = self._get_baud_value()
        handle = start_channel(chn)
        if handle is None:
            messagebox.showerror("错误", f"启动 CAN{chn} 通道失败！")
            return

        chn_handles[chn] = handle
        rx_count[chn] = 0
        tx_count[chn] = 0

        # 启动接收线程
        t = threading.Thread(target=self._receive_loop, args=(chn, handle), daemon=True)
        t.start()

        self._update_channel_status(chn, True)
        self._update_status_bar()
        self._update_chn_combo()
        self._add_sys_message(f"CAN{chn} 通道已启动")

    def _do_start_all_channels(self):
        """启动全部通道"""
        for chn in [0, 1]:
            if chn not in chn_handles:
                self._do_start_channel(chn)

    def _do_stop_channel(self, chn):
        """停止指定通道"""
        if chn not in chn_handles:
            return
        handle = chn_handles.pop(chn, None)
        if handle is not None:
            zcanlib.ResetCAN(handle)
        rx_count.pop(chn, None)
        tx_count.pop(chn, None)

        self._update_channel_status(chn, False)
        self._update_status_bar()
        self._update_chn_combo()
        self._add_sys_message(f"CAN{chn} 通道已停止")

    def _do_stop_all_channels(self):
        """停止全部通道"""
        for chn in list(chn_handles.keys()):
            self._do_stop_channel(chn)

    def _update_channel_status(self, chn, active):
        """更新通道启动按钮和状态标签"""
        if chn == 0:
            if active:
                self.chn0_start_btn.configure(text="停止 CAN0", fg_color="#F44336", hover_color="#D32F2F",
                                              command=lambda: self._do_stop_channel(0))
                self.chn0_status_label.configure(text="运行中", text_color="#4CAF50")
            else:
                self.chn0_start_btn.configure(text="启动 CAN0", fg_color="#4CAF50", hover_color="#388E3C",
                                              command=lambda: self._do_start_channel(0))
                self.chn0_status_label.configure(text="未启动", text_color="#888888")
        elif chn == 1:
            if active:
                self.chn1_start_btn.configure(text="停止 CAN1", fg_color="#F44336", hover_color="#D32F2F",
                                              command=lambda: self._do_stop_channel(1))
                self.chn1_status_label.configure(text="运行中", text_color="#4CAF50")
            else:
                self.chn1_start_btn.configure(text="启动 CAN1", fg_color="#4CAF50", hover_color="#388E3C",
                                              command=lambda: self._do_start_channel(1))
                self.chn1_status_label.configure(text="未启动", text_color="#888888")

    def _update_status_bar(self):
        """更新状态栏中的通道指示器"""
        for label in self.channel_labels.values():
            label.destroy()
        self.channel_labels.clear()

        for i, chn in enumerate(sorted(chn_handles.keys())):
            indicator = ctk.CTkLabel(
                self.status_frame,
                text=f"  ● CAN{chn}  ",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color="#4CAF50"
            )
            indicator.pack(side="left", padx=(10 if i == 0 else 0, 0))
            self.channel_labels[chn] = indicator

        if chn_handles:
            baud_label = ctk.CTkLabel(
                self.status_frame,
                text=f"  {BAUD_RATE} bps",
                font=ctk.CTkFont(size=12),
                text_color="#888888"
            )
            baud_label.pack(side="left", padx=(15, 0))

    def _update_status_bar_clear(self):
        """清空状态栏指示器"""
        for label in self.channel_labels.values():
            label.destroy()
        self.channel_labels.clear()

    def _update_chn_combo(self):
        """更新发送面板的通道选择下拉框"""
        active_channels = sorted(chn_handles.keys())
        if active_channels:
            values = [f"CAN{c}" for c in active_channels]
            self.chn_combo.configure(values=values)
            self.chn_combo.set(values[0])
        else:
            self.chn_combo.configure(values=["--"])
            self.chn_combo.set("--")

    def _add_sys_message(self, text):
        """添加系统消息到报文区"""
        entry = {
            'timestamp': int(time.time() * 1000000),
            'chn': 0,
            'direction': "SYS",
            'can_id': 0,
            'frame_type': "标准帧",
            'frame_format': "数据帧",
            'dlc': 0,
            'data': text
        }
        self.msg_queue.append(entry)

    def _receive_loop(self, chn, chn_handle):
        """接收线程"""
        while thread_flag:
            time.sleep(0.005)
            try:
                rcv_num = zcanlib.GetReceiveNum(chn_handle, ZCAN_TYPE_CAN)
                if rcv_num:
                    if rcv_num > 100:
                        rcv_msg, rcv_num = zcanlib.Receive(chn_handle, 100, 100)
                    else:
                        rcv_msg, rcv_num = zcanlib.Receive(chn_handle, rcv_num, 100)
                    for msg in rcv_msg[:rcv_num]:
                        frame = msg.frame
                        direction = "TX" if frame._pad & 0x20 else "RX"
                        frame_type = "扩展帧" if frame.can_id & (1 << 31) else "标准帧"
                        frame_format = "远程帧" if frame.can_id & (1 << 30) else "数据帧"
                        can_id = frame.can_id & 0x1FFFFFFF

                        if frame.can_id & (1 << 30):
                            data_str = ""
                            dlc = 0
                        else:
                            dlc = frame.can_dlc
                            data_str = " ".join([f"{num:02X}" for num in frame.data[:dlc]])

                        rx_count[chn] = rx_count.get(chn, 0) + 1

                        entry = {
                            'timestamp': msg.timestamp,
                            'chn': chn,
                            'direction': direction,
                            'can_id': can_id,
                            'frame_type': frame_type,
                            'frame_format': frame_format,
                            'dlc': dlc,
                            'data': data_str
                        }
                        self.msg_queue.append(entry)
            except Exception:
                pass

    def _poll_messages(self):
        """定时刷新报文显示 (50ms)"""
        if not thread_flag:
            return

        new_messages = []
        while self.msg_queue:
            try:
                entry = self.msg_queue.popleft()
                new_messages.append(entry)
            except IndexError:
                break

        if new_messages:
            self.msg_text.configure(state="normal")
            for entry in new_messages:
                self._append_message(entry)
                self.line_count += 1

            # 限制行数
            if self.line_count > MAX_LOG_LINES:
                self.msg_text._textbox.delete("1.0", f"{self.line_count - MAX_LOG_LINES}.0")
                self.line_count = MAX_LOG_LINES

            self.msg_text.configure(state="disabled")

            # 自动滚动
            if self.auto_scroll:
                self.msg_text._textbox.see("end")

        # 更新统计
        self._update_stats()

        self.after(50, self._poll_messages)

    def _append_message(self, entry):
        """添加一条报文到显示区"""
        if entry['direction'] == "SYS":
            # 系统消息：完整一行显示
            line = f"-- {entry['data']} --\n"
            tag = "sys"
        else:
            ts = str(entry['timestamp'])[-10:].ljust(12)
            direction = entry['direction'].ljust(4)
            chn_str = f"CAN{entry['chn']}".ljust(6)
            is_ext = "扩展" in entry['frame_type']
            if is_ext:
                id_str = f"0x{entry['can_id']:08X}".ljust(12)
            else:
                id_str = f"0x{entry['can_id']:03X}".ljust(12)
            ft = "EXT" if is_ext else "STD"
            ff = "RTR" if "远程" in entry['frame_format'] else "DAT"
            type_str = f"{ft} {ff}".ljust(8)
            dlc_str = str(entry['dlc']).ljust(4)
            data_str = entry['data']
            line = f"{ts}{direction}{chn_str}{id_str}{type_str}{dlc_str}{data_str}\n"

            tag = "rx" if entry['direction'] == "RX" else "tx"

        self.msg_text._textbox.insert("end", line, tag)

    def _update_stats(self):
        """更新收发统计"""
        if not chn_handles:
            return
        stats_parts = []
        for chn in chn_handles:
            r = rx_count.get(chn, 0)
            t = tx_count.get(chn, 0)
            stats_parts.append(f"CAN{chn} RX:{r} TX:{t}")
        self.stats_label.configure(text="  │  ".join(stats_parts))

    def _update_dlc(self, event=None):
        """实时更新DLC显示"""
        data_str = self.data_entry.get().strip()
        if not data_str:
            self.dlc_label.configure(text="0")
            return
        try:
            parts = data_str.split()
            dlc = len(parts)
            self.dlc_label.configure(text=str(min(dlc, 8)))
        except Exception:
            pass


    def _do_send(self):
        """执行发送"""
        # 解析通道
        chn_text = self.chn_combo.get()
        try:
            chn = int(chn_text.replace("CAN", ""))
        except ValueError:
            messagebox.showerror("错误", "请选择有效的通道")
            return

        if chn not in chn_handles:
            messagebox.showerror("错误", f"CAN{chn} 未打开")
            return

        # 解析ID
        id_text = self.id_entry.get().strip()
        if not id_text:
            messagebox.showerror("错误", "请输入CAN ID")
            return
        try:
            can_id = int(id_text, 16)
            if can_id < 0 or can_id > 0x1FFFFFFF:
                messagebox.showerror("错误", "ID超出范围 (0 ~ 0x1FFFFFFF)")
                return
        except ValueError:
            messagebox.showerror("错误", "无效的十六进制ID")
            return

        # 解析数据
        data_text = self.data_entry.get().strip()
        data_bytes = []
        if data_text:
            try:
                data_bytes = [int(x, 16) for x in data_text.split()]
                if len(data_bytes) > 8:
                    messagebox.showerror("错误", "数据最多8字节")
                    return
                if not all(0 <= b <= 255 for b in data_bytes):
                    messagebox.showerror("错误", "每字节范围 0x00 ~ 0xFF")
                    return
            except ValueError:
                messagebox.showerror("错误", "无效的十六进制数据")
                return

        is_extended = self.ext_var.get()

        # 发送
        success = send_can_message(chn_handles[chn], can_id, data_bytes, is_extended)
        if success:
            tx_count[chn] = tx_count.get(chn, 0) + 1

            # 将TX报文添加到显示区
            data_str = " ".join([f"{b:02X}" for b in data_bytes]) if data_bytes else ""
            frame_type = "扩展帧" if is_extended else "标准帧"
            frame_format = "数据帧"
            tx_entry = {
                'timestamp': int(time.time() * 1000000),
                'chn': chn,
                'direction': "TX",
                'can_id': can_id,
                'frame_type': frame_type,
                'frame_format': frame_format,
                'dlc': len(data_bytes),
                'data': data_str
            }
            self.msg_queue.append(tx_entry)

            # 记录历史
            record = {'chn': chn, 'id': can_id, 'data': data_bytes, 'extended': is_extended}
            send_history.append(record)
            self._update_history_combo()

            # 显示发送反馈（闪烁按钮颜色）
            self.send_btn.configure(fg_color="#2E7D32", text="已发送 ✓")
            self.after(800, lambda: self.send_btn.configure(fg_color="#4CAF50", text="发 送"))
        else:
            self.send_btn.configure(fg_color="#D32F2F", text="失败 ✗")
            self.after(800, lambda: self.send_btn.configure(fg_color="#4CAF50", text="发 送"))

    def _update_history_combo(self):
        """更新历史下拉框"""
        if not send_history:
            return
        items = []
        for i, r in enumerate(send_history[-20:], 1):
            data_hex = " ".join([f"{b:02X}" for b in r['data']]) if r['data'] else "(空)"
            frame_desc = "EXT" if r['extended'] else "STD"
            items.append(f"{i}. CAN{r['chn']} 0x{r['id']:X} {frame_desc} [{data_hex}]")
        self.history_combo.configure(values=items)
        self.history_combo.set(items[-1])

    def _do_resend(self):
        """重发历史记录"""
        selection = self.history_combo.get()
        if not selection or selection == "(无历史记录)":
            return
        try:
            idx = int(selection.split(".")[0]) - 1
            records = send_history[-20:]
            if 0 <= idx < len(records):
                r = records[idx]
                if r['chn'] in chn_handles:
                    success = send_can_message(chn_handles[r['chn']], r['id'], r['data'], r['extended'])
                    if success:
                        tx_count[r['chn']] = tx_count.get(r['chn'], 0) + 1
                        self.resend_btn.configure(text="✓", fg_color="#4CAF50")
                        self.after(600, lambda: self.resend_btn.configure(text="重发", fg_color="#1F6AA5"))
                    else:
                        self.resend_btn.configure(text="✗", fg_color="#D32F2F")
                        self.after(600, lambda: self.resend_btn.configure(text="重发", fg_color="#1F6AA5"))
        except (ValueError, IndexError):
            pass

    def _fill_from_history(self):
        """从历史记录填充到输入框"""
        selection = self.history_combo.get()
        if not selection or selection == "(无历史记录)":
            return
        try:
            idx = int(selection.split(".")[0]) - 1
            records = send_history[-20:]
            if 0 <= idx < len(records):
                r = records[idx]
                # 填入通道
                self.chn_combo.set(f"CAN{r['chn']}")
                # 填入ID
                self.id_entry.delete(0, "end")
                self.id_entry.insert(0, f"{r['id']:X}")
                # 填入扩展帧
                self.ext_var.set(r['extended'])
                # 填入数据
                self.data_entry.delete(0, "end")
                if r['data']:
                    self.data_entry.insert(0, " ".join([f"{b:02X}" for b in r['data']]))
                self._update_dlc()
        except (ValueError, IndexError):
            pass

    def _clear_messages(self):
        """清空报文显示"""
        self.msg_text.configure(state="normal")
        self.msg_text._textbox.delete("1.0", "end")
        self.msg_text.configure(state="disabled")
        self.line_count = 0

    def _toggle_scroll(self):
        """切换自动滚动"""
        self.auto_scroll = self.scroll_var.get()

    def _reset_counts(self):
        """重置计数"""
        for chn in chn_handles:
            rx_count[chn] = 0
            tx_count[chn] = 0

    def _on_close(self):
        """窗口关闭"""
        global thread_flag
        thread_flag = False
        time.sleep(0.1)
        cleanup()
        self.destroy()



# ========== 主程序 ==========

if __name__ == "__main__":
    zcanlib = ZCAN()
    app = CanMonitorApp()
    app.mainloop()

    # 确保退出时清理
    thread_flag = False
    cleanup()
