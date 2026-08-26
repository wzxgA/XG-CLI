from textual.widgets import Static


class FooterBar(Static):
    def __init__(self) -> None:
        super().__init__("Enter 发送   Ctrl+C 取消   Ctrl+L 清屏   Ctrl+R 侧栏   Esc 关闭弹窗", id="footer")
