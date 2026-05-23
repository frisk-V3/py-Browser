import tkinter as tk
from tkinter import scrolledtext, messagebox
import threading
import requests
from html.parser import HTMLParser
from PIL import Image, ImageTk
from io import BytesIO
import re
from urllib.parse import urljoin

# ============ JavaScript実行エンジン判定 ============
try:
    from quickjs import Context
    JS_ENABLED = True
except Exception:
    JS_ENABLED = False

# ============ HTMLパーサー ============
class DOMNode:
    def __init__(self, tag, attrs=None, text=""):
        self.tag = tag
        self.attrs = dict(attrs) if attrs else {}
        self.text = text
        self.children = []
        self.style = {}
    def add_child(self, node):
        self.children.append(node)

class SimpleHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = DOMNode("html")
        self.current = self.root
        self.stack = [self.root]
        self.images = []
    def handle_starttag(self, tag, attrs):
        node = DOMNode(tag, attrs)
        self.current.add_child(node)
        if tag not in ['img', 'br', 'hr', 'input']:
            self.stack.append(node)
            self.current = node
    def handle_endtag(self, tag):
        if self.stack and self.stack[-1].tag == tag:
            self.stack.pop()
            self.current = self.stack[-1]
    def handle_data(self, data):
        if data.strip():
            text_node = DOMNode("text", text=data.strip())
            self.current.add_child(text_node)
    def get_dom(self):
        return self.root

# ============ レンダリングエンジン ============
class RenderEngine:
    def __init__(self, width=800, height=600):
        self.width = width
        self.height = height
        self.y_offset = 0
        self.base_url = ""
        self.images_data = {}
    def render_dom(self, dom, base_url=""):
        self.base_url = base_url
        self.y_offset = 0
        content = self._render_node(dom)
        return content
    def _render_node(self, node):
        content = ""
        if node.tag == "text":
            return node.text + " "
        elif node.tag == "img":
            url = node.attrs.get('src', '')
            if url:
                full_url = urljoin(self.base_url, url)
                self.images_data[full_url] = None
                content += f"[IMAGE: {full_url}]\n"
        elif node.tag in ["h1", "h2", "h3"]:
            text = self._get_text(node)
            content += f"\n{'=' * len(text)}\n{text}\n{'=' * len(text)}\n"
        elif node.tag == "p":
            text = self._get_text(node)
            content += text + "\n\n"
        elif node.tag == "a":
            href = node.attrs.get('href', '#')
            text = self._get_text(node)
            content += f"[LINK: {text}] ({href})"
        elif node.tag == "script":
            return content
        elif node.tag in ["div", "body", "html", "main"]:
            for child in node.children:
                content += self._render_node(child)
        else:
            for child in node.children:
                content += self._render_node(child)
        return content
    def _get_text(self, node):
        text = ""
        for child in node.children:
            if child.tag == "text":
                text += child.text + " "
            else:
                text += self._get_text(child)
        return text.strip()

# ============ JS 実行用ラッパー ============
class JSContext:
    def __init__(self, console_callback=None):
        self.console_callback = console_callback
        self.context = None
        if JS_ENABLED:
            try:
                self.context = Context()
                # export console.log to call into Python via a simple function
                # QuickJS binding of functions can be complex; keep simple:
                self.context.eval("var console = { log: function(){ return undefined; } };")
            except Exception as e:
                self.context = None
    def execute(self, code):
        if not self.context:
            return "QuickJS not available"
        try:
            # QuickJS returns values; convert to str
            result = self.context.eval(code)
            return str(result)
        except Exception as e:
            return f"JS Error: {e}"

# ============ Python フォールバック（簡易） ============
class PyJSFallback:
    def __init__(self, console_callback=None):
        self.console_callback = console_callback
    def execute(self, code):
        code = code.strip()
        # handle console.log("...") or console.log('...')
        m = re.match(r'console\.log\((.*)\)\s*;?$', code, re.DOTALL)
        if m:
            payload = m.group(1).strip()
            s = re.match(r'^[\'"](.*)[\'"]$', payload, re.DOTALL)
            if s:
                msg = s.group(1)
                if self.console_callback:
                    self.console_callback(msg)
                return msg
            else:
                # try to evaluate as numeric/simple expression
                expr = payload
                if re.match(r'^[0-9+\-*/ ().]+$', expr):
                    try:
                        val = eval(expr)
                        if self.console_callback:
                            self.console_callback(str(val))
                        return str(val)
                    except Exception as e:
                        return f"Eval error: {e}"
                return "Unsupported console.log argument"
        # evaluate("1+2")
        m = re.match(r'evaluate\((.*)\)\s*;?$', code, re.DOTALL)
        if m:
            inner = m.group(1).strip().strip('\'"')
            if re.match(r'^[0-9+\-*/ ().]+$', inner):
                try:
                    val = eval(inner)
                    return str(val)
                except Exception as e:
                    return f"Eval error: {e}"
            else:
                return "Unsafe expression"
        # simple alert("msg")
        m = re.match(r'alert\((.*)\)\s*;?$', code, re.DOTALL)
        if m:
            msg = m.group(1).strip().strip('\'"')
            if self.console_callback:
                self.console_callback(f"[alert] {msg}")
            return f"[alert] {msg}"
        return "Unsupported JS in fallback"

# ============ メインブラウザUI ============
class SimpleBrowser:
    def __init__(self, root):
        self.root = root
        self.root.title("Simple Browser with JS Console")
        self.root.geometry("1000x700")
        self.current_url = ""
        # choose JS backend
        if JS_ENABLED:
            self.js_context = JSContext(console_callback=self.log_console)
        else:
            self.js_context = PyJSFallback(console_callback=self.log_console)
        self.render_engine = RenderEngine()
        # トップ
        top_frame = tk.Frame(root, bg="lightgray", height=50)
        top_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        tk.Label(top_frame, text="URL:", bg="lightgray").pack(side=tk.LEFT, padx=5)
        self.url_entry = tk.Entry(top_frame, width=60)
        self.url_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.url_entry.bind("<Return>", lambda e: self.load_page())
        tk.Button(top_frame, text="Go", command=self.load_page, bg="lightblue").pack(side=tk.LEFT, padx=5)
        tk.Button(top_frame, text="DevTools", command=self.toggle_console, bg="lightyellow").pack(side=tk.LEFT, padx=5)
        # メイン
        self.main_frame = tk.Frame(root)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.content_text = scrolledtext.ScrolledText(self.main_frame, wrap=tk.WORD, state=tk.DISABLED, font=("Courier", 10))
        self.content_text.pack(fill=tk.BOTH, expand=True)
        # コンソール（初期非表示）
        self.console_visible = False
        self.console_frame = tk.Frame(root, bg="black", height=180)
        console_label = tk.Label(self.console_frame, text="Console", bg="gray", fg="white")
        console_label.pack(side=tk.TOP, fill=tk.X)
        self.console_text = scrolledtext.ScrolledText(self.console_frame, wrap=tk.WORD, bg="black", fg="white", font=("Courier",9), height=8)
        self.console_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        js_input_frame = tk.Frame(self.console_frame, bg="black")
        js_input_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)
        tk.Label(js_input_frame, text="JS:", bg="black", fg="white").pack(side=tk.LEFT)
        self.js_input = tk.Entry(js_input_frame, bg="darkgray", fg="white", font=("Courier",9))
        self.js_input.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.js_input.bind("<Return>", lambda e: self.execute_js())
        tk.Button(js_input_frame, text="Run", command=self.execute_js, bg="green", fg="white").pack(side=tk.LEFT)
        # キーバインド（Ctrl+Shift+J / Ctrl+Shift+I）
        root.bind_all('<Control-Shift-J>', lambda e: self.toggle_console())
        root.bind_all('<Control-Shift-j>', lambda e: self.toggle_console())
        root.bind_all('<Control-Shift-I>', lambda e: self.toggle_console())
        root.bind_all('<Control-Shift-i>', lambda e: self.toggle_console())
        # 初期ログ
        if JS_ENABLED:
            self.log_console("QuickJS available — console uses QuickJS.")
        else:
            self.log_console("QuickJS not available — using Python fallback console.")
        self.log_console("Press Ctrl+Shift+J or Ctrl+Shift+I to toggle console.")
    def load_page(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("Error", "Please enter a URL")
            return
        if not url.startswith("http"):
            url = "https://" + url
        self.log_console(f"Loading {url}...")
        thread = threading.Thread(target=self._fetch_and_render, args=(url,))
        thread.daemon = True
        thread.start()
    def _fetch_and_render(self, url):
        try:
            self.log_console(f"Fetching: {url}")
            response = requests.get(url, timeout=10)
            response.encoding = 'utf-8'
            html = response.text
            self.current_url = url
            parser = SimpleHTMLParser()
            parser.feed(html)
            dom = parser.get_dom()
            self.render_engine.base_url = url
            content = self.render_engine.render_dom(dom, url)
            self._load_images()
            self.display_content(content)
            # Do NOT auto-execute scripts found in HTML; only show they exist
            scripts = self._extract_scripts(html)
            if scripts:
                self.log_console(f"Found {len(scripts)} <script> blocks (not auto-executed). Use console to run JS.")
            self.log_console(f"\n✓ Page loaded successfully")
        except requests.exceptions.RequestException as e:
            self.log_console(f"✗ Network Error: {e}")
        except Exception as e:
            self.log_console(f"✗ Error: {e}")
    def _extract_scripts(self, html):
        pattern = r'<script[^>]*>(.*?)</script>'
        scripts = re.findall(pattern, html, re.DOTALL | re.IGNORECASE)
        return [s.strip() for s in scripts if s.strip()]
    def _load_images(self):
        for img_url in list(self.render_engine.images_data.keys()):
            try:
                response = requests.get(img_url, timeout=5)
                self.render_engine.images_data[img_url] = response.content
                self.log_console(f"Loaded image: {img_url}")
            except:
                pass
    def display_content(self, content):
        self.content_text.config(state=tk.NORMAL)
        self.content_text.delete(1.0, tk.END)
        self.content_text.insert(1.0, content)
        self.content_text.config(state=tk.DISABLED)
    def toggle_console(self):
        if self.console_visible:
            self.console_frame.pack_forget()
            self.console_visible = False
        else:
            self.console_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)
            self.console_visible = True
    def log_console(self, message):
        self.console_text.config(state=tk.NORMAL)
        self.console_text.insert(tk.END, message + "\n")
        self.console_text.see(tk.END)
        self.console_text.config(state=tk.DISABLED)
    def execute_js(self):
        code = self.js_input.get().strip()
        if not code:
            return
        self.log_console(f"> {code}")
        result = self.js_context.execute(code)
        self.log_console(f"< {result}")
        self.js_input.delete(0, tk.END)

# ============ 実行 ============
if __name__ == "__main__":
    root = tk.Tk()
    browser = SimpleBrowser(root)
    root.mainloop()
