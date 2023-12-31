#!/usr/bin/env python3
import asyncio
from asyncio import Semaphore
from typing import Awaitable, Iterable, Optional, Set, Tuple

# 总尝试时间 = TIMEOUT × RETRY_TIMES
TIMEOUT = 5
RETRY_TIMES = 3
# 协程数量
SEMAPHORE_THREADHOLD = 32
# 判定为成功的 HTTP 状态码
ALLOW_HTTP_STATUS_CODE = (200, 403)
# 读入 URL 模式，可选 pipe 和 file
TRACKER_INPUT_METHOD = "PIPE"
TRACKER_URLS_FILE = r"./urls.txt"


class CompatibleAliveProgress:
    
    def try_import(self):
        """尝试导入 alive_progress.alive_bar

        Returns:
            导入的 module
        """
        
        try:
            import importlib
            alive_bar = importlib.import_module("alive_progress").alive_bar
            
            return alive_bar
        
        except ImportError:
            return None
    

    def __init__(self, *args, **kwargs):
        
        def init_context_manager():
            self.manager = self.alive_bar(*args, **kwargs)
            self.enter = type(self.manager).__enter__
            self.exit = type(self.manager).__exit__
        
        
        self.alive_bar = self.try_import()
        
        if self.alive_bar:
            init_context_manager()
        
        self.bar = None


    def __enter__(self):
        if self.alive_bar:
            self.bar = self.enter(self.manager)
            return self.bar
        
        else:
            def func_pass(*args, **kwargs):
                pass
            return func_pass


    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.alive_bar:
            self.exit(self.manager, exc_type, exc_val, exc_tb)


alive_bar = CompatibleAliveProgress


def retry(func: Awaitable):
    """让函数重试 RETRY_TIMES 次。
    函数返回 None 则直接返回 None；
    若返回结果则将结果加入 list 中，最后返回该 list

    Arguments:
        func -- _description_
    """
    
    async def _wrapper(*args, **kwargs) -> Optional[Set]:
        _result_list = list()
        
        for i in range(RETRY_TIMES):
            _result = await func(*args, **kwargs)
            
            if _result is None:
                # 其中有一次成功则立刻跳出循环
                return None
            else:
                # 否则将结果加入结果列表，继续循环
                if isinstance(_result, RuntimeError):
                    # raise RuntimeError 方便 Debug
                    raise _result
                _result_list.append(_result)
        
        return _result_list
    
    return _wrapper


@retry
async def check_udp_tracker_url(url: str) -> Optional[Exception]:
    import random
    import struct
    from urllib.parse import urlsplit
    
    
    def init_udp_socket(timeout=TIMEOUT):
        import socket
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        return sock
    
    
    def gen_id() -> Tuple[int, int]:
        """生成一个随机的事务id和固定的连接id

        Returns:
            事务id、连接id
        """
        return random.randint(0, 2**32 - 1), 0x41727101980
    
    
    def url_split(url) -> Tuple[str, int]:
        """解析url，获取主机名、端口号和announce路径

        Returns:
            host, port
        """
        
        scheme, netloc, path, query, fragment = urlsplit(url)
        assert scheme == "udp"
        
        host, port = netloc.split(":")
        port = int(port)
        
        return host, port
    
    
    host, port = url_split(url)
    sock = init_udp_socket()

    transaction_id, connection_id = gen_id()
    
    async def send_connect(_transaction_id: int, _connection_id: int) \
        -> Optional[Exception]:
        """尝试链接一个tracker

        Returns:
            成功返回 None，否则返回 Exception
        """
        _result = None
        
        try:
            # 向tracker发送一个连接请求
            conn_req = struct.pack(">QLL", _connection_id, 0, _transaction_id)
            sock.sendto(conn_req, (host, port))

            # 从tracker接收一个连接响应
            loop = asyncio.get_event_loop()
            conn_resp = await asyncio.wait_for(
                loop.sock_recv(sock, 16),
                timeout=TIMEOUT + 5
            )
            action, resp_transaction_id, _connection_id = \
                struct.unpack(">LLQ", conn_resp)

            # 检查 action 和事务 id 是否有效
            if action != 0 or resp_transaction_id != _transaction_id:
                _result = Exception("Invalid connection response")
            
        except Exception as e:
            _result = e

        finally:
            sock.close()
            return _result
    
    return await send_connect(transaction_id, connection_id)


@retry
async def check_http_tracker_url(url) -> Optional[Exception]:
    """检测HTTP tracker URL是否可用

    Returns:
        成功返回 None，否则返回 Exception
    """
    
    def init_client_session():
        import aiohttp
        return aiohttp.ClientSession()
    
    _result = None
    my_client_session = init_client_session()
    
    # 创建一个请求参数字典，包含一些必要的字段
    params = {
        "info_hash": (b"\x00" * 20).decode('utf-8'), # 一个随机的20字节的信息哈希值
        "peer_id": (b"\x00" * 20).decode('utf-8'), # 一个随机的20字节的对等节点ID
        "port": 6881, # 一个随机的端口号
        "uploaded": 0, # 已上传的字节数
        "downloaded": 0, # 已下载的字节数
        "left": 0, # 剩余的字节数
        "compact": 1, # 是否使用紧凑模式
        "event": "started" # 事件类型，表示开始下载
    }
    
    try:
        # 发送一个get请求到指定的url，并传递参数字典
        status_code = (
            await my_client_session.get(url, params=params, timeout=TIMEOUT)
        ).status
        # 检查响应状态码是否为200，表示成功
        if status_code not in ALLOW_HTTP_STATUS_CODE:
            _result = Exception(f"status code: {status_code}")
            
    except Exception as e:
        # 捕获任何可能发生的异常，并打印错误信息
        _result = e
        
    finally:
        await my_client_session.close()
        return _result


def read_urls_from_pipe(method: str):
    """读入 URL 数据

    Arguments:
        method -- 可选值：PIPE | FILE
            PIPE: 从 stdin 读入 url list
            FILE: 从 TRACKER_URLS_FILE 路径读入纯文本文件

    Returns:
        url 列表
    """
    
    urls = list()
    stream = list()
    
    match method.lower():
        case "pipe":
            import fileinput
            stream = fileinput.input()

        case "file":
            stream = open(TRACKER_URLS_FILE, 'r')

        case _:
            raise ValueError("Not supported input method!")

    for line in stream:
        urls.append(line.strip()) if line.strip() else "pass"
    
    stream.close()
    
    if len(urls) > 1:
        return urls
    else:
        raise Exception("No tracker url loaded!")

sem = Semaphore(SEMAPHORE_THREADHOLD)
tracker_urls = read_urls_from_pipe(TRACKER_INPUT_METHOD)

async def check_tracker_url(tracker_url, bar) -> Optional[Exception]:
    """遍历tracker URL列表，根据URL的协议类型，调用相应的检测函数

    Arguments:
        tracker_url -- 要检测的 tracker 地址

    Returns:
        成功返回 None，否则返回 Exception 错误信息。
    """
    async with sem:
        if tracker_url.startswith("udp://"):
            _result = await check_udp_tracker_url(tracker_url)
        elif tracker_url.startswith("http://") or tracker_url.startswith("https://"):
            _result = await check_http_tracker_url(tracker_url)
        else:
            _result = ValueError("Scheme not supported.")
        
        print(f"Success! {tracker_url}" if _result is None
            else f"{tracker_url}: {_result}")
        bar()
        
        return _result


async def main():
    
    def Exception_list_to_str_set(it: Optional[Iterable]):
        return set(map(
            lambda item : repr(item),
            it
        )) if isinstance(it, Iterable) else it
    
    
    with alive_bar(len(tracker_urls)) as bar:
        tasks = [asyncio.create_task(check_tracker_url(tracker_url, bar))
                 for tracker_url in tracker_urls]
        result_list = await asyncio.gather(*tasks, return_exceptions=True)
    
    result_list = map(Exception_list_to_str_set, result_list)
    
    return result_list
    

result_list = asyncio.run(main())
tracker_status = tuple(zip(tracker_urls, result_list))


print("\nGood Urls\n"+ "=" * 20)
for item in filter(lambda item: item[1] is None, tracker_status):
    print(item[0])

print("\nBad Urls\n"+ "=" * 20)
for item in filter(lambda item: item[1] is not None, tracker_status):
    print(f"{item[0]} | {repr(item[1])}")


