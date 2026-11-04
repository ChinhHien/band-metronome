"""Module AI đọc bố cục bài hát → gợi ý JSON cho web.

Tách biệt có chủ đích: app.py chỉ biết tới đúng hàm register() dưới đây, và mọi thứ
tắt khi thiếu `AI_LAYOUT_ENABLED=1`. Server không giữ credential — key/base URL/model
do từng thiết bị nhập trên web và gửi kèm từng request.
"""
from .routes import bp


def register(app):
    """Gắn blueprint /api/ai/*. Xong là xong — app.py không có tham số nào khác."""
    app.register_blueprint(bp)
    return bp
