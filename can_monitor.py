# 交互式CAN通道控制台

from zlgcan import *
import threading
import time
import signal
import sys
import os

# ========== 配置区 ==========
BAUD_RATE = "500000"    # 波特率
# ============================

thread_flag = True
device_handle = None
zcanlib = None
chn_handles = {}  # {通道号: 句柄}
rx_count = {}     # {通道号: 接收计数}
tx_count = {}     # {通道号: 发送计数}
print_lock = threading.Lock()


# ========== 界面工具函数 ==========

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def print_banner():
    clear_screen()
    print("\033[36m")  # 青色
    print("  ╔══════════════════════════════════════════════════╗")
    print("  ║           ZLG CAN 交互式控制台                  ║")
    print("  ╠══════════════════════════════════════════════════╣")
    print("  ║  设备: USBCAN-II    波特率: %s bps       ║" % BAUD_RATE.ljust(7))
    print("  ╚══════════════════════════════════════════════════╝")
    print("\033[0m")


def print_status():
    """打印当前状态栏"""
    opened = ", ".join([f"\033[32m●\033[0m CAN{c}" for c in chn_handles]) if chn_handles else "\033[31m● 无\033[0m"
    print(f"  通道状态: {opened}")
    stats = "  ".join([f"CAN{c}: RX={rx_count.get(c,0)} TX={tx_count.get(c,0)}" for c in chn_handles])
    if stats:
        print(f"  收发统计: {stats}")
    print()


def print_separator():
    print("\033[90m  " + "─" * 48 + "\033[0m")


def print_menu():
    """打印主菜单"""
    print()
    print_separator()
    print_status()
    print_separator()
    print("  \033[33m[S]\033[0m 发送报文    \033[33m[H]\033[0m 发送历史    \033[33m[C]\033[0m 清屏")
    print("  \033[33m[R]\033[0m 重置计数    \033[33m[Q]\033[0m 退出程序")
    print_separator()


def print_success(msg):
    print(f"\033[32m  ✓ {msg}\033[0m")


def print_error(msg):
    print(f"\033[31m  ✗ {msg}\033[0m")


def print_info(msg):
    print(f"\033[36m  ℹ {msg}\033[0m")


def print_rx_message(chn, timestamp, direction, can_id, frame_type, frame_format, dlc, data):
    """格式化打印接收到的CAN报文"""
    dir_color = "\033[33m" if direction == "TX" else "\033[32m"
    with print_lock:
        print(f"\r  {dir_color}{direction}\033[0m │ CAN{chn} │ "
              f"ID: \033[97m{can_id}\033[0m │ "
              f"{frame_type} {frame_format} │ "
              f"DLC: {dlc} │ "
              f"\033[96m{data}\033[0m"
              f"  \033[90m[{timestamp}]\033[0m")


# ========== 核心功能 ==========

send_history = []  # 发送历史记录


def cleanup():
    """清理资源：关闭所有通道和设备"""
    global thread_flag
    thread_flag = False
    time.sleep(0.05)

    print()
    for chn, handle in chn_handles.items():
        if handle is not None:
            ret = zcanlib.ResetCAN(handle)
            if ret == 1:
                print_success(f"CAN{chn} 通道已关闭")

    if device_handle is not None:
        ret = zcanlib.CloseDevice(device_handle)
        if ret == 1:
            print_success("设备已关闭")


def signal_handler(sig, frame):
    """处理 Ctrl+C 等信号"""
    print("\n")
    print_info("收到退出信号，正在关闭...")
    cleanup()
    sys.exit(0)


def receive_thread(chn, chn_handle):
    """接收线程：持续监听指定CAN通道数据"""
    id_width = len(hex(0x1FFFFFFF))

    while thread_flag:
        time.sleep(0.005)
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
                can_id = hex(frame.can_id & 0x1FFFFFFF)

                if frame.can_id & (1 << 30):
                    data = ""
                    dlc = 0
                else:
                    dlc = frame.can_dlc
                    data = " ".join([f"{num:02X}" for num in frame.data[:dlc]])

                rx_count[chn] = rx_count.get(chn, 0) + 1
                print_rx_message(chn, msg.timestamp, direction,
                                 f"{can_id:<{id_width}}", frame_type, frame_format, dlc, data)


def start_channel(chn):
    """启动指定CAN通道，返回通道句柄"""
    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/baud_rate", BAUD_RATE.encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print_error(f"设置CAN{chn}波特率失败")
        return None

    chn_init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
    chn_init_cfg.can_type = ZCAN_TYPE_CAN
    chn_init_cfg.config.can.mode = 0
    chn_init_cfg.config.can.acc_code = 0
    chn_init_cfg.config.can.acc_mask = 0xFFFFFFFF

    chn_handle = zcanlib.InitCAN(device_handle, chn, chn_init_cfg)
    if chn_handle is None:
        print_error(f"初始化CAN{chn}通道失败")
        return None

    ret = zcanlib.StartCAN(chn_handle)
    if ret != ZCAN_STATUS_OK:
        print_error(f"启动CAN{chn}通道失败")
        return None

    print_success(f"CAN{chn} 通道已启动 (波特率: {BAUD_RATE} bps, 正常模式)")
    return chn_handle


def send_can_message(chn_handle, can_id, data_bytes, is_extended=False, is_remote=False):
    """发送一条CAN报文"""
    msgs = (ZCAN_Transmit_Data * 1)()
    memset(addressof(msgs), 0, sizeof(msgs))

    msgs[0].transmit_type = 0
    msgs[0].frame.can_id = can_id & 0x1FFFFFFF
    if is_extended:
        msgs[0].frame.can_id |= (1 << 31)
    if is_remote:
        msgs[0].frame.can_id |= (1 << 30)

    if not is_remote:
        msgs[0].frame.can_dlc = len(data_bytes)
        for i, b in enumerate(data_bytes):
            msgs[0].frame.data[i] = b

    ret = zcanlib.Transmit(chn_handle, msgs, 1)
    return ret == 1


def select_channels():
    """让用户选择要打开的通道"""
    print()
    print("  请选择要打开的通道:")
    print()
    print("    \033[33m[0]\033[0m  仅 CAN0")
    print("    \033[33m[1]\033[0m  仅 CAN1")
    print("    \033[33m[2]\033[0m  同时打开 CAN0 + CAN1")
    print()

    while True:
        choice = input("  请输入选择 \033[90m(0/1/2)\033[0m: ").strip()
        if choice == "0":
            return [0]
        elif choice == "1":
            return [1]
        elif choice == "2":
            return [0, 1]
        else:
            print_error("无效输入，请重新选择")


def interactive_send():
    """交互式发送CAN报文"""
    if not chn_handles:
        print_error("没有已打开的通道！")
        return

    print()
    print_separator()
    print("  \033[97m发送 CAN 报文\033[0m")
    print_separator()

    # 选择通道
    available = list(chn_handles.keys())
    if len(available) == 1:
        chn = available[0]
        print_info(f"使用 CAN{chn} 通道发送")
    else:
        while True:
            choice = input(f"  选择发送通道 \033[90m({'/'.join([str(c) for c in available])})\033[0m: ").strip()
            try:
                chn = int(choice)
                if chn in available:
                    break
            except ValueError:
                pass
            print_error("无效输入，请重新选择")

    # 输入ID
    while True:
        id_str = input("  CAN ID \033[90m(十六进制，如 1A3)\033[0m: ").strip()
        if id_str.lower() == 'q':
            return
        try:
            can_id = int(id_str, 16)
            if 0 <= can_id <= 0x1FFFFFFF:
                break
            print_error("ID超出范围 (0 ~ 0x1FFFFFFF)")
        except ValueError:
            print_error("无效的十六进制ID")

    # 是否扩展帧
    is_extended = can_id > 0x7FF
    ext_input = input(f"  扩展帧? \033[90m(y/n, 默认{'y' if is_extended else 'n'})\033[0m: ").strip().lower()
    if ext_input == 'y':
        is_extended = True
    elif ext_input == 'n':
        is_extended = False

    # 输入数据
    while True:
        data_str = input("  数据 \033[90m(十六进制空格分隔，如 01 02 FF，最多8字节)\033[0m: ").strip()
        if data_str.lower() == 'q':
            return
        if not data_str:
            data_bytes = []
            break
        try:
            data_bytes = [int(x, 16) for x in data_str.split()]
            if len(data_bytes) > 8:
                print_error("数据最多8字节")
                continue
            if all(0 <= b <= 255 for b in data_bytes):
                break
            print_error("每字节范围 0x00 ~ 0xFF")
        except ValueError:
            print_error("无效的十六进制数据")

    # 发送
    success = send_can_message(chn_handles[chn], can_id, data_bytes, is_extended)
    if success:
        tx_count[chn] = tx_count.get(chn, 0) + 1
        id_hex = f"0x{can_id:X}"
        data_hex = " ".join([f"{b:02X}" for b in data_bytes]) if data_bytes else "(空)"
        frame_desc = "扩展帧" if is_extended else "标准帧"
        print_success(f"已发送 -> CAN{chn} | ID: {id_hex} | {frame_desc} | DATA: [{data_hex}]")
        # 记录历史
        send_history.append({
            'chn': chn, 'id': can_id, 'data': data_bytes,
            'extended': is_extended
        })
    else:
        print_error("发送失败！请检查总线状态。")


def show_send_history():
    """显示发送历史"""
    if not send_history:
        print_info("暂无发送记录")
        return

    print()
    print_separator()
    print("  \033[97m发送历史 (最近10条)\033[0m")
    print_separator()
    for i, record in enumerate(send_history[-10:], 1):
        data_hex = " ".join([f"{b:02X}" for b in record['data']]) if record['data'] else "(空)"
        frame_desc = "扩展帧" if record['extended'] else "标准帧"
        print(f"  {i:2d}. CAN{record['chn']} │ ID: 0x{record['id']:X} │ {frame_desc} │ DATA: [{data_hex}]")
    print_separator()

    # 快捷重发
    if send_history:
        choice = input("  输入序号快捷重发 \033[90m(回车跳过)\033[0m: ").strip()
        if choice:
            try:
                idx = int(choice) - 1
                records = send_history[-10:]
                if 0 <= idx < len(records):
                    r = records[idx]
                    success = send_can_message(chn_handles[r['chn']], r['id'], r['data'], r['extended'])
                    if success:
                        tx_count[r['chn']] = tx_count.get(r['chn'], 0) + 1
                        print_success("重发成功")
                    else:
                        print_error("重发失败")
            except (ValueError, KeyError):
                print_error("无效序号")


def reset_counters():
    """重置收发计数"""
    for chn in chn_handles:
        rx_count[chn] = 0
        tx_count[chn] = 0
    print_success("收发计数已重置")


# ========== 主程序 ==========

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    zcanlib = ZCAN()

    print_banner()

    # 打开设备
    device_handle = zcanlib.OpenDevice(ZCAN_USBCAN2, 0, 0)
    if device_handle == INVALID_DEVICE_HANDLE:
        print_error("打开设备失败！请检查设备连接。")
        exit(0)
    print_success(f"设备已打开 (句柄: {device_handle})")

    # 获取设备信息
    info = zcanlib.GetDeviceInf(device_handle)
    print_info(f"设备信息: {info}")

    # 选择通道
    channels = select_channels()

    # 启动通道
    threads = []
    for chn in channels:
        handle = start_channel(chn)
        if handle is None:
            print_error(f"启动CAN{chn}失败，退出")
            cleanup()
            exit(0)
        chn_handles[chn] = handle
        rx_count[chn] = 0
        tx_count[chn] = 0

        t = threading.Thread(target=receive_thread, args=(chn, handle), daemon=True)
        threads.append(t)
        t.start()

    print()
    print_info("接收线程已启动，数据将实时显示")
    print_info("输入命令进行操作，输入过程中收到的数据也会实时打印")

    # 主循环
    try:
        while True:
            print_menu()
            try:
                cmd = input("  \033[97m>>>\033[0m ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                break

            if cmd == 's':
                interactive_send()
            elif cmd == 'h':
                show_send_history()
            elif cmd == 'c':
                clear_screen()
                print_banner()
                print_info("接收数据监听中...")
            elif cmd == 'r':
                reset_counters()
            elif cmd in ('q', 'quit', 'exit'):
                break
            elif cmd == '':
                continue
            else:
                print_error(f"未知命令: {cmd}，请输入 S/H/C/R/Q")
    except (EOFError, KeyboardInterrupt):
        pass

    # 退出
    print()
    print_info("正在关闭...")
    cleanup()
    for t in threads:
        t.join(timeout=1)
    print()
    print("  \033[36m再见！\033[0m")
    print()
