"""Kiểm tra base URL do người dùng nhập trước khi server gọi ra ngoài.

Ở phương án "mỗi thiết bị mang key riêng", server trở thành cầu nối tới một địa chỉ
do *khách* chọn — phải coi đó là đầu vào không tin cậy: chỉ https (key không đi
clear-text), không userinfo, không port lạ, không trỏ vào mạng nội bộ / metadata
cloud, và nếu `AI_ALLOWED_HOSTS` được đặt thì chỉ đúng các host trong danh sách.

Còn cửa sổ TOCTOU giữa lúc phân giải DNS và lúc SDK kết nối; muốn đóng tuyệt đối thì
chạy production với `AI_ALLOWED_HOSTS` (khoá theo tên host mà provider sở hữu).
"""
import ipaddress
import socket
import urllib.parse

_BLOCKED_ATTRS = ("is_private", "is_loopback", "is_link_local", "is_reserved",
                  "is_multicast", "is_unspecified")


class GuardError(ValueError):
    """URL không đạt chuẩn an toàn — server từ chối gọi ra ngoài."""


def host_of(url):
    """Tên host đã viết thường, hoặc chuỗi rỗng nếu URL không parse được."""
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def check_base_url(url, allowed_hosts=()):
    """Chuẩn hoá và kiểm tra an toàn; trả về URL bỏ dấu `/` thừa ở cuối."""
    url = (url or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise GuardError("Base URL phải bắt đầu bằng https:// (key không được đi dạng không mã hoá).")
    if not parsed.hostname:
        raise GuardError("Base URL không có tên máy chủ.")
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise GuardError("Base URL không được chứa thông tin đăng nhập.")
    if parsed.port not in (None, 443):
        raise GuardError("Base URL chỉ được dùng cổng 443.")

    host = parsed.hostname.lower()
    if allowed_hosts and host not in {h.lower() for h in allowed_hosts}:
        raise GuardError(f"Host “{host}” ngoài danh sách cho phép của server này.")

    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise GuardError(f"Không phân giải được host “{host}”: {e}") from e
    if not infos:
        raise GuardError(f"Không tìm thấy máy chủ cho host “{host}”.")
    for *_, sock in infos:
        ip = ipaddress.ip_address(sock[0])
        if any(getattr(ip, attr, False) for attr in _BLOCKED_ATTRS):
            raise GuardError(f"Host “{host}” trỏ tới địa chỉ nội bộ ({ip}) — bị chặn.")

    return url.rstrip("/")
