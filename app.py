#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import requests
from datetime import datetime, timedelta, timezone
from seleniumbase import SB
from selenium.common.exceptions import ElementClickInterceptedException, WebDriverException, StaleElementReferenceException
from selenium.webdriver.common.by import By
from zoneinfo import ZoneInfo

EMAIL = os.getenv('EMAIL') or ""
PASSWORD = os.getenv('PASSWORD') or ""
TG_CHAT_ID = os.getenv('TG_CHAT_ID') or ""
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN') or ""

LOGIN_PATH = '/auth/login'
BASE_URL = 'https://aclclouds.com'
PROJECTS_URL = f'{BASE_URL}/dashboard/projects'
RENEWALS_URL = f'{BASE_URL}/dashboard/projects?view=renewals'

SUCCESS_KEYWORDS = ('successfully', 'avec succès', 'réussi', 'succès', '成功')


def beijing_time_str():
    try:
        return datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')


def send_telegram(message):
    if TG_BOT_TOKEN and TG_CHAT_ID:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = {'chat_id': TG_CHAT_ID, 'text': message}
        try:
            requests.post(url, data=data, timeout=10)
            print(f"Telegram sent:\n{message}")
        except Exception as e:
            print(f"Failed to send Telegram: {e}")
    else:
        print(f"[Telegram disabled] {message}")


def wait_for_url_change(sb, original_url, timeout=30):
    start_time = time.time()
    while time.time() - start_time < timeout:
        if sb.get_current_url() != original_url:
            return True
        sb.sleep(0.5)
    raise Exception(f"等待 URL 变化超时 ({timeout}秒)，当前仍为: {original_url}")


def is_login_page(sb):
    return LOGIN_PATH in sb.get_current_url()


def is_logged_in(sb):
    url = sb.get_current_url()
    return '/dashboard' in url and '/auth/login' not in url


def scroll_to_selector(sb, selector):
    sb.scroll_to(selector)
    sb.sleep(0.2)


def safe_click_element(sb, element, label):
    try:
        sb.driver.execute_script(
            'arguments[0].scrollIntoView({block: "center", inline: "center"});',
            element,
        )
        sb.sleep(0.5)
        try:
            element.click()
            return True
        except (ElementClickInterceptedException, WebDriverException, StaleElementReferenceException) as e:
            print(f"{label} 普通点击失败，改用 JavaScript 点击: {e}")
        sb.driver.execute_script('arguments[0].click();', element)
        sb.sleep(0.5)
        return True
    except StaleElementReferenceException:
        print(f"{label} 元素已失效，点击前需要重新定位")
        return False


def mask_email(email):
    if not email or '@' not in email:
        return email or ''
    local, domain = email.split('@', 1)
    if len(local) <= 2:
        masked_local = local[0] + '****' if local else '****'
    elif len(local) <= 4:
        masked_local = f"{local[0]}****{local[-1]}"
    else:
        masked_local = f"{local[:2]}****{local[-2:]}"
    return f"{masked_local}@{domain}"


# ==================== 登录相关（完全参考你提供的旧脚本） ====================

def click_captcha_checkbox(sb, label='验证码', timeout=10):
    """点击 ACLClouds 页面上的人机验证复选框，并处理图形验证码挑战。"""
    selectors = [
        'div.auth-captcha-inner[role="checkbox"]',
        '//div[contains(., "Anti-bot confirmation")]//*[@role="checkbox"]',
        '//div[contains(., "I am not a robot")]//*[@role="checkbox"]',
        '//div[contains(@class, "modal") and contains(., "Secured by ACLClouds")]//*[@role="checkbox"]',
    ]

    last_error = None
    clicked = False
    selector = None
    for candidate in selectors:
        try:
            sb.wait_for_element_visible(candidate, timeout=timeout)
            scroll_to_selector(sb, candidate)
            sb.uc_click(candidate)
            sb.sleep(1)
            selector = candidate
            clicked = True
            break
        except Exception as e:
            last_error = e
            continue

    if not clicked:
        print(f"{label} 点击复选框失败: {last_error}")
        return False

    # 给 5 秒加载缓冲，避免图形验证码尚未渲染完成时就开始点击
    sb.sleep(5)
    captcha_ok = handle_captcha_challenge(sb, label, timeout=20)
    if not captcha_ok:
        print(f"{label} 验证流程未完成，等待状态仍未确认。")
        return False

    try:
        checked = sb.get_attribute(selector, 'aria-checked')
        if checked == 'true':
            print(f"{label} 验证通过")
            return True
        else:
            print(f"{label} 验证未完成，当前状态: {checked}")
            return False
    except Exception:
        return False


def handle_captcha_challenge(sb, label='验证码', timeout=20):
    """处理图形验证码挑战：先等待挑战加载，再尝试点击对应图像。"""
    start_time = time.time()
    challenge = None
    last_error = None
    challenge_selectors = [
        '.auth-captcha-challenge',
        '.auth-capcha-challenge',
        '//*[contains(@class, "captcha") and contains(@class, "challenge")]',
        '//*[contains(@aria-label, "Click on ") or contains(@aria-label, "Select ") or contains(@class, "challenge")]',
    ]

    def get_challenge():
        for selector in challenge_selectors:
            try:
                if selector.startswith('/'):
                    elems = sb.driver.find_elements(By.XPATH, selector)
                    for elem in elems:
                        if elem.is_displayed():
                            return elem
                else:
                    elem = sb.wait_for_element_visible(selector, timeout=1)
                    if elem and elem.is_displayed():
                        return elem
            except Exception:
                continue
        return None

    while time.time() - start_time < timeout:
        challenge = get_challenge()
        if challenge:
            print(f"{label} 检测到图形验证码挑战")
            break
        try:
            checkbox = sb.driver.find_element(By.CSS_SELECTOR, 'div.auth-captcha-inner[role="checkbox"]')
            if checkbox.get_attribute('aria-checked') == 'true':
                print(f"{label} 验证复选框已勾选，验证码流程已完成")
                return True
        except Exception:
            pass
        sb.sleep(0.3)

    if not challenge:
        print(f"{label} 等待验证码挑战加载超时: {last_error}")
        return False

    target = ''
    try:
        prompt = challenge.find_element(By.CSS_SELECTOR, '.auth-captcha-prompt strong')
        target = prompt.text.strip()
    except Exception:
        pass
    if not target:
        try:
            prompt = challenge.find_element(By.CSS_SELECTOR, '.auth-capcha-prompt strong')
            target = prompt.text.strip()
        except Exception:
            pass
    if not target:
        aria_label = challenge.get_attribute('aria-label') or ''
        if 'Click on ' in aria_label:
            target = aria_label.split('Click on ')[-1].strip()

    print(f"{label} 目标文本: {target or '未识别'}")

    option_selectors = [
        '.auth-captcha-option',
        '.auth-capcha-option',
        './/button',
        './/a',
        './/div[@role="button"]',
    ]

    def get_options(challenge_elem):
        for sel in option_selectors:
            try:
                if sel.startswith('.') or sel.startswith('['):
                    elems = challenge_elem.find_elements(By.CSS_SELECTOR, sel)
                else:
                    elems = challenge_elem.find_elements(By.XPATH, sel)
                if elems:
                    return [elem for elem in elems if elem.is_displayed() and elem.is_enabled()]
            except Exception:
                continue
        return []

    options = get_options(challenge)
    if not options:
        print(f"{label} 未找到可点击的选项")
        return False

    attempts = 0
    max_attempts = 8
    while attempts < max_attempts:
        challenge = get_challenge()
        if not challenge:
            return False

        options = get_options(challenge)
        if not options:
            print(f"{label} 当前挑战没有可点击选项，重试中...")
            attempts += 1
            sb.sleep(0.8)
            continue

        current_target = ''
        try:
            prompt = challenge.find_element(By.CSS_SELECTOR, '.auth-captcha-prompt strong')
            current_target = prompt.text.strip()
        except Exception:
            pass
        if not current_target:
            aria_label = challenge.get_attribute('aria-label') or ''
            if 'Click on ' in aria_label:
                current_target = aria_label.split('Click on ')[-1].strip()

        candidate = None
        if target and current_target and current_target.lower() == target.lower():
            for opt in options:
                opt_text = (opt.text or '').strip()
                if not opt_text:
                    try:
                        img = opt.find_element(By.TAG_NAME, 'img')
                        opt_text = (img.get_attribute('alt') or '').strip()
                    except Exception:
                        pass
                if not opt_text:
                    try:
                        opt_text = (opt.get_attribute('aria-label') or '').strip()
                    except Exception:
                        pass
                if target.lower() in opt_text.lower():
                    candidate = opt
                    break

        if candidate is None:
            candidate = options[0]

        print(f"{label} 点击候选选项 #{attempts + 1} ...")
        clicked = safe_click_element(sb, candidate, f"{label} 选项候选")
        if not clicked:
            attempts += 1
            sb.sleep(0.8)
            continue

        sb.sleep(1.2)

        try:
            checkbox = sb.driver.find_element(By.CSS_SELECTOR, 'div.auth-captcha-inner[role="checkbox"]')
            if checkbox.get_attribute('aria-checked') == 'true':
                print(f"{label} 验证复选框已勾选，验证码流程已完成")
                return True
        except Exception:
            pass

        if not get_challenge():
            print(f"{label} 挑战已消失，验证完成")
            return True

        attempts += 1

    print(f"{label} 多次尝试后仍未完成验证码")
    return False


def js_set_input_value(sb, selector, value):
    sb.execute_script(
        '''
        const el = document.querySelector(arguments[0]);
        if (!el) return false;
        el.focus();
        el.value = arguments[1];
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.dispatchEvent(new Event('blur', { bubbles: true }));
        return true;
        ''',
        selector, value,
    )


def fill_input(sb, selector, value, label, timeout=15):
    sb.wait_for_element_visible(selector, timeout=timeout)
    scroll_to_selector(sb, selector)
    sb.click(selector)
    sb.clear(selector)
    sb.type(selector, value)

    entered_value = sb.get_value(selector)
    if label == '密码':
        print(f"{label}输入框当前值长度: {len(entered_value)}")
    else:
        print(f"{label}输入框当前值: '{entered_value}'")

    if entered_value != value:
        print(f"{label}输入未生效，使用 JavaScript 强制赋值")
        js_set_input_value(sb, selector, value)
        entered_value = sb.get_value(selector)

    return entered_value == value


def login(sb, email, password):
    """执行登录（不做重试），返回是否成功。"""
    print("开始登录流程...")

    # 填写邮箱
    if not fill_input(sb, '#username', email, '邮箱'):
        print("⚠️ 邮箱仍未能正确填入，可能页面有动态行为。")

    # 填写密码
    if not fill_input(sb, '#password', password, '密码'):
        print("⚠️ 密码仍未能正确填入。")

    # 验证码
    captcha_ok = click_captcha_checkbox(sb, '登录验证码')
    if not captcha_ok:
        print("⚠️ 登录验证码未完成，暂不点击登录按钮，避免直接提交。")
        return False

    sb.sleep(1)

    # 点击登录按钮
    login_page_url = sb.get_current_url()
    clicked = False

    for selector in ['button[type="submit"]', 'div.auth-submit-btn',
                     '//button[contains(text(), "Sign in")]',
                     '//div[contains(text(), "Sign in")]']:
        try:
            sb.wait_for_element_visible(selector, timeout=5)
            scroll_to_selector(sb, selector)
            sb.click(selector)
            clicked = True
            print(f"点击 Sign in 使用: {selector}")
            break
        except Exception as e:
            print(f"选择器 {selector} 失败: {e}")
    if not clicked:
        print("所有选择器失败，使用 JS 点击")
        sb.execute_script('''
            var els = document.querySelectorAll('div, button, a');
            for (var el of els) {
                if (el.textContent.trim() === 'Sign in') {
                    el.click();
                    return true;
                }
            }
            return false;
        ''')

    # 等待登录结果
    try:
        wait_for_url_change(sb, login_page_url, timeout=30)
        current = sb.get_current_url()
        if '/auth/login' not in current and '/dashboard' in current:
            print(f"✅ 登录成功！URL: {current}")
            return True
        else:
            error_msg = ""
            try:
                errors = sb.driver.find_elements(By.CSS_SELECTOR, '.auth-error-text, .alert-danger, .error-message')
                error_msg = errors[0].text.strip() if errors else ''
            except Exception:
                pass
            print(f"❌ 登录失败，当前: {current}，错误: {error_msg}")
            return False
    except Exception as e:
        print(f"登录过程异常: {e}")
        try:
            errors = sb.driver.find_elements(By.CSS_SELECTOR, '.auth-error-text, .alert-danger, .error-message')
            if errors:
                print(f"页面错误信息: {errors[0].text.strip()}")
        except Exception:
            pass
        return False


def handle_renew_antibot(sb, project_name):
    for selector in [
        '//div[contains(., "Anti-bot confirmation")]',
        '//div[contains(., "Confirm you are human")]',
        '//div[contains(., "I am not a robot")]',
    ]:
        try:
            sb.wait_for_element_visible(selector, timeout=5)
            print(f"[{project_name}] 检测到续期人机验证窗口")
            return click_captcha_checkbox(sb, '续期人机验证', timeout=5)
        except Exception:
            continue
    print(f"[{project_name}] 未检测到续期人机验证窗口")
    return False


def get_current_ip(proxy_server: str = "") -> str:
    proxies = None
    if proxy_server:
        normalized = proxy_server.replace("socks://", "socks5h://")
        proxies = {"http": normalized, "https": normalized}
    response = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
    response.raise_for_status()
    return response.text.strip()


# ==================== 通知消息 ====================

def build_success_message(project_name, old_expiry, new_expiry):
    return "\n".join([
        "🇫🇷 Aclclouds 续期通知", "",
        "✅ 续期成功",
        f"📦 项目: {project_name}",
        f"⏱️ 旧过期: {old_expiry}",
        f"⏱️ 新过期: {new_expiry}",
        f"👤 登录账户: {mask_email(EMAIL)}",
        f"⏱️ 运行时间: {beijing_time_str()}",
    ])


def build_not_yet_due_message(project_name, expiry, note=''):
    lines = [
        "🇫🇷 Aclclouds 续期通知", "",
        "⏳ 未到续期时间",
        f"📦 项目: {project_name}",
        f"⏱️ 当前过期时间: {expiry}",
    ]
    if note:
        lines.append(f"📅 可续期提示: {note}")
    lines.extend([
        f"👤 登录账户: {mask_email(EMAIL)}",
        f"⏱️ 运行时间: {beijing_time_str()}",
    ])
    return "\n".join(lines)


def build_unconfirmed_message(project_name, old_expiry, new_expiry, result_note):
    lines = [
        "🇫🇷 Aclclouds 续期通知", "",
        f"❌ 续期状态未确认: {project_name}",
        f"👤 登录账户: {mask_email(EMAIL)}",
    ]
    if old_expiry:
        lines.append(f"旧过期: {old_expiry}")
    lines.extend([
        f"当前过期: {new_expiry}",
        f"页面提示: {result_note or '未发现成功提示'}",
    ])
    return "\n".join(lines)


# ==================== 详情页处理 ====================

def read_detail_page_info(sb):
    name = ''
    expiry = ''
    renewal_note = ''

    try:
        for h in sb.driver.find_elements(By.CSS_SELECTOR, 'h1, h2, h3'):
            t = (h.text or '').strip()
            if t and len(t) <= 80 \
                    and not re.search(r'^(console|version|files|databases|schedules|users|backups|network|domains|startup|settings|activity|support|documentation|menu)$', t, re.I) \
                    and not re.search(r'renew|time remaining', t, re.I):
                name = t
                break
    except Exception:
        pass

    if not name:
        try:
            title = sb.get_title()
            m = re.match(r'^([^|]+?)\s*\|', title)
            if m:
                name = m.group(1).strip()
        except Exception:
            pass

    try:
        expiry = sb.driver.execute_script('''
            const t = document.body.innerText || '';
            const m = t.match(/(?:Time remaining|Temps restant)[:：]\\s*([^\\n]+)/i);
            return m ? m[1].trim() : '';
        ''') or ''
    except Exception:
        pass

    if not expiry:
        try:
            body = sb.driver.find_element(By.TAG_NAME, 'body').text
            m = re.search(
                r'(?:Time remaining|Temps restant)[:：]?\s*'
                r'(\d+\s*(?:d|j|days?|jours?|h|hours?|heures?|天|小时)\s*'
                r'\d*\s*(?:h|hours?|heures?|小时)?)',
                body, re.I
            )
            if m:
                expiry = m.group(1).strip()
        except Exception:
            pass

    try:
        renewal_note = sb.driver.execute_script('''
            const t = document.body.innerText || '';
            const m = t.match(/((?:Free|paid|Basic|Pro)[^\\n]*(?:renew|renouvel)[^\\n]*)/i);
            return m ? m[1].trim() : '';
        ''') or ''
    except Exception:
        pass

    return name, expiry, renewal_note


def find_renew_button_on_detail(sb):
    try:
        btns = sb.driver.find_elements(By.XPATH, '//button')
        for b in btns:
            try:
                t = (b.text or '').strip()
                if t in ('Renew', 'Renouveler', '续期', '续订') and b.is_displayed() and b.is_enabled():
                    return b
            except Exception:
                continue
    except Exception:
        pass
    return None


def should_try_renew(expiry_str):
    if not expiry_str:
        return False
    m = re.match(r'(\d+)\s*(?:j|d|jour|jours|days?|天|日)\b', expiry_str, re.I)
    if m:
        return int(m.group(1)) <= 2
    m = re.match(r'(\d+)\s*(?:h|heure|heures|hour|hours|小时)', expiry_str, re.I)
    if m:
        return int(m.group(1)) <= 48
    return False


# ==================== 主流程 ====================

def main():
    IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
    PROXY_SERVER = os.getenv('S5_PROXY') or os.getenv('PROXY_SERVER') or "socks5://127.0.0.1:1080"
    if PROXY_SERVER.startswith("socks://"):
        PROXY_SERVER = PROXY_SERVER.replace("socks://", "socks5h://", 1)
    HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"
    sb_options = {'uc': True, 'headless': HEADLESS}
    if IS_PROXY:
        sb_options['proxy'] = PROXY_SERVER
        print(f"🔗 挂载代理: {PROXY_SERVER}")
    else:
        print("🍭 未使用代理，直连访问")

    with SB(**sb_options) as sb:
        try:
            ip = get_current_ip(PROXY_SERVER if IS_PROXY else "")
            print(f"📍 当前出口IP: {ip}")
        except Exception as e:
            print(f"获取出口IP失败: {e}")

        sb.set_window_size(1920, 1080)

        print(f"打开续期视图: {RENEWALS_URL}")
        sb.open(RENEWALS_URL)
        sb.wait_for_ready_state_complete()
        time.sleep(2)

        if not is_logged_in(sb):
            print("未登录，前往登录页...")
            sb.open(f"{BASE_URL}{LOGIN_PATH}")
            sb.wait_for_ready_state_complete()
            time.sleep(2)
            if not is_login_page(sb):
                print("❌ 无法打开登录页")
                send_telegram("⚠️ 无法打开登录页")
                return
            if not EMAIL or not PASSWORD:
                print("❌ 未配置 EMAIL 或 PASSWORD")
                send_telegram("⚠️ 未配置 EMAIL 或 PASSWORD。")
                return
            if not login(sb, EMAIL, PASSWORD):
                return
            sb.open(RENEWALS_URL)
            sb.wait_for_ready_state_complete()
            time.sleep(3)
        else:
            print(f"✅ 当前已登录: {sb.get_current_url()}")

        if 'view=renewals' not in sb.get_current_url():
            sb.open(RENEWALS_URL)
            sb.wait_for_ready_state_complete()
            time.sleep(3)

        print("等待续期视图渲染...")
        start = time.time()
        while time.time() - start < 20:
            try:
                found = sb.driver.execute_script('''
                    const bodyText = document.body.innerText || '';
                    const re = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
                    return re.test(bodyText) || /Renouveler|Renew|Expire/.test(bodyText);
                ''')
                if found:
                    break
            except Exception:
                pass
            sb.sleep(0.5)
        sb.sleep(1)

        links = sb.driver.execute_script('''
            const out = [];
            document.querySelectorAll('a[href*="/server/"]').forEach(a => {
                const href = a.getAttribute('href') || '';
                const m = href.match(/\\/server\\/([0-9a-f-]+)/i);
                if (m) out.push({uuid: m[1].toLowerCase(), href: href});
            });
            const seen = new Set();
            const uniq = [];
            for (const x of out) {
                if (seen.has(x.uuid)) continue;
                seen.add(x.uuid);
                uniq.push(x);
            }
            return uniq;
        ''')

        if not links:
            m = re.search(r'/server/([0-9a-f-]+)', sb.get_current_url(), re.I)
            if m:
                links = [{'uuid': m.group(1).lower(), 'href': sb.get_current_url()}]

        if not links:
            print("❌ 未找到任何 /server/ 链接")
            try:
                body = sb.driver.find_element(By.TAG_NAME, 'body').text
                print("页面文本摘要：")
                print(body[:1500])
            except Exception:
                pass
            send_telegram("⚠️ 未找到服务器详情链接")
            return

        print(f"找到 {len(links)} 个服务器")

        for item in links:
            uuid = item['uuid']
            href = item['href']
            if not href.startswith('http'):
                href = BASE_URL + href

            print(f"\n--- 处理 {href} ---")
            try:
                sb.open(href)
                sb.wait_for_ready_state_complete()
                sb.sleep(2.5)
            except Exception as e:
                print(f"  打开失败: {e}")
                continue

            name, expiry, renewal_note = read_detail_page_info(sb)
            print(f"  项目名: {name or '未知'}")
            print(f"  剩余时间: {expiry or '未知'}")
            print(f"  续期规则: {renewal_note or '未知'}")

            renew_btn = find_renew_button_on_detail(sb)
            print(f"  Renew 按钮: {'有' if renew_btn else '无'}")

            should = should_try_renew(expiry)

            if renew_btn and should:
                print(f"  剩余 {expiry}，点击 Renew 按钮...")
                safe_click_element(sb, renew_btn, f"[{name}] Renew")
                handle_renew_antibot(sb, name)
                sb.sleep(5)

                try:
                    sb.open(href)
                    sb.wait_for_ready_state_complete()
                    sb.sleep(2)
                    _, new_expiry, _ = read_detail_page_info(sb)
                except Exception:
                    new_expiry = expiry

                if new_expiry and new_expiry != expiry:
                    print(f"  ✅ 续期成功！{expiry} → {new_expiry}")
                    send_telegram(build_success_message(name, expiry, new_expiry))
                else:
                    print(f"  ⚠️ 续期状态未确认，过期时间: {new_expiry}")
                    send_telegram(build_unconfirmed_message(name, expiry, new_expiry or expiry, '按钮已点击'))
            elif renew_btn:
                print(f"  未到续期时间（剩余 {expiry}）")
                send_telegram(build_not_yet_due_message(name, expiry, renewal_note or 'Renewal will be available 2 days before expiration'))
            else:
                print(f"  未找到 Renew 按钮")
                send_telegram(build_not_yet_due_message(name, expiry, renewal_note or ''))

        print("\n所有项目处理完成。")


if __name__ == '__main__':
    main()
