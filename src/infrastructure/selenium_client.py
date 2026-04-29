from __future__ import annotations

import time
from dataclasses import dataclass
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import os
from shutil import which
from selenium import webdriver


@dataclass(frozen=True)
class SeleniumConfig:
    default_timeout: int = 180


class SeleniumClient:
    """
    Owns webdriver lifecycle + login.
    Based on existing working implementation. :contentReference[oaicite:6]{index=6}
    """

    def __init__(self, config: SeleniumConfig):
        self.config = config
        self.driver = None
        self.wait = None

    def start(self) -> None:
        import tempfile

        chrome_options = Options()

        # -----------------------------
        # COMMON (Works everywhere)
        # -----------------------------
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        # 🔥 CRITICAL FIX (avoid session conflict)
        chrome_options.add_argument(f"--user-data-dir={tempfile.mkdtemp()}")

        # -----------------------------
        # ENV DETECTION
        # -----------------------------
        chromium_path = which("chromium-browser")
        chromedriver_path = which("chromedriver")

        if chromium_path and chromedriver_path:
            # ✅ Raspberry Pi / Linux
            chrome_options.binary_location = chromium_path
            chrome_options.add_argument("--headless=new")

            service = Service(chromedriver_path)

        else:
            # ✅ Windows / Laptop
            from webdriver_manager.chrome import ChromeDriverManager

            # optional: comment if you want UI
            # chrome_options.add_argument("--headless=new")

            service = Service(ChromeDriverManager().install())

        # -----------------------------
        # DRIVER START
        # -----------------------------
        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        self.wait = WebDriverWait(self.driver, self.config.default_timeout)




    def login(self, login_url: str, user: str, password: str) -> None:
        if not self.driver or not self.wait:
            raise RuntimeError("SeleniumClient not started. Call start() first.")

        self.driver.maximize_window()
        self.driver.get(login_url)
        self.wait.until(lambda d: d.execute_script("return document.readyState") == "complete")

        # Same approach as your existing code. :contentReference[oaicite:7]{index=7}
        username_input = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@type='text']")))
        password_input = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@type='password']")))

        username_input.clear()
        username_input.send_keys(user)

        password_input.clear()
        password_input.send_keys(password + Keys.ENTER)

        time.sleep(4)

    def stop(self) -> None:
        if self.driver:
            self.driver.quit()
        self.driver = None
        self.wait = None
