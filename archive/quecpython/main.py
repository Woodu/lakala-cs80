"""CS80 设备端开机动作（放到 /usr/main.py，QuecPython 启动时自动跑）。

现在数据通道走 **REPL + zlib 帧**（见项目根 README §6），设备端只需要：
  1. 点屏（MIPI + 背光）
  2. 清成深色，等 PC 端 cs80_status.py 把第一帧贴上来

`net_up()` / `serve()` 是早前 "USBNET + TCP 远程控制" 方案的遗留：
无 SIM 时 `USBNET.open()` 恒为 -1，ECM 载波起不来，所以 boot() 里不再调用。
"""

import sys
import utime

sys.path.append('/usr')

import cs80  # noqa: E402


def net_diag():
    try:
        import sim
        print('sim:', sim.getStatus())
    except Exception as e:
        print('sim fail', e)
    try:
        import dataCall
        print('dataCall:', dataCall.getInfo(1, 0))
    except Exception as e:
        print('dataCall fail', e)


def net_up():
    from misc import USBNET
    try:
        cur = USBNET.get_worktype()
        print('usbnet worktype:', cur)
        if cur != USBNET.Type_ECM:
            USBNET.set_worktype(USBNET.Type_ECM)
            print('set ECM, reboot needed')
    except Exception as e:
        print('worktype fail', e)
    try:
        r = USBNET.open()
        print('USBNET.open ->', r)
    except Exception as e:
        print('USBNET.open fail', e)
    utime.sleep_ms(1500)
    try:
        print('USBNET status:', USBNET.get_status())
    except Exception as e:
        print('status fail', e)
    net_diag()


def http_save(host, port, path, out_path):
    """极简 HTTP/1.0 GET，把响应体存到 out_path。"""
    import usocket as socket
    s = socket.socket()
    try:
        s.settimeout(10)
    except Exception:
        pass
    s.connect((host, int(port)))
    req = 'GET %s HTTP/1.0\r\nHost: %s\r\nConnection: close\r\n\r\n' % (path, host)
    s.send(req.encode())
    buf = b''
    while True:
        d = s.recv(2048)
        if not d:
            break
        buf += d
    s.close()

    i = buf.find(b'\r\n\r\n')
    if i < 0:
        raise OSError('bad http response')
    head, body = buf[:i], buf[i + 4:]
    if b'200' not in head.split(b'\r\n')[0]:
        raise OSError(head.split(b'\r\n')[0])

    f = open(out_path, 'wb')
    f.write(body)
    f.close()
    return len(body)


def handle(line):
    parts = line.split()
    if not parts:
        return ''
    cmd = parts[0].lower()

    if cmd == 'ping':
        return 'pong'
    if cmd == 'info':
        try:
            from misc import USBNET
            st = USBNET.get_status()
        except Exception:
            st = '?'
        return 'usbnet=%s lcd=%s' % (st, 'on' if cs80._lcd else 'off')
    if cmd == 'clear':
        color = parts[1] if len(parts) > 1 else '0xFFFF'
        cs80.backlight(1)
        return 'clear -> %s' % cs80.clear(color)
    if cmd == 'show':
        host, port, path = parts[1], parts[2], parts[3]
        n = http_save(host, port, path, '/usr/frame.jpg')
        cs80.backlight(1)
        cs80.show('/usr/frame.jpg')
        return 'shown %d bytes' % n
    if cmd == 'exec':
        import ubinascii
        code = ubinascii.a2b_base64(parts[1]).decode()
        ns = {'__name__': 'remote'}
        exec(code, ns)
        return 'exec ok'
    if cmd == 'reboot':
        import misc
        misc.reset()
        return 'rebooting'
    return 'unknown: %s' % cmd


def serve():
    """（遗留）TCP 控制服务：需要 USBNET 可用（插卡有 PDP）才能开到 PC。"""
    PORT = 9000
    import usocket as socket
    s = socket.socket()
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except Exception:
        pass
    s.bind(('0.0.0.0', PORT))
    s.listen(2)
    print('listening on', PORT)
    while True:
        try:
            conn, addr = s.accept()
        except Exception as e:
            print('accept fail', e)
            utime.sleep_ms(500)
            continue
        try:
            conn.settimeout(15)
            data = b''
            while b'\n' not in data and len(data) < 4096:
                d = conn.recv(512)
                if not d:
                    break
                data += d
            line = data.decode('latin-1').strip()
            print('cmd:', line[:80])
            resp = handle(line)
            conn.send((resp + '\n').encode())
        except Exception as e:
            try:
                conn.send(('error: %s\n' % e).encode())
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass


def boot():
    # 只点屏 + 清底，数据由 PC 端（win-display/cs80_status.py）经 REPL 推上来
    try:
        cs80.init_panel()
        cs80.backlight(1)
        cs80.clear(0x0000)
        print('panel up, waiting for frames')
    except Exception as e:
        print('panel fail', e)


if __name__ == '__main__':
    boot()
