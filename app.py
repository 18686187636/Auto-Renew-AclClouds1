def find_project_cards(sb):
    cards = []

    # ===== 原有逻辑：Manage 按钮 =====
    try:
        manage_btns = find_manage_buttons(sb.driver)
    except Exception:
        manage_btns = []

    for btn in manage_btns:
        try:
            card = find_card_container_from_child(sb, btn)
            if card is not None:
                cards.append(card)
        except Exception:
            continue

    # ===== 原有逻辑：续期按钮 =====
    if not cards:
        try:
            for button in find_renew_buttons(sb.driver):
                try:
                    card = find_card_container_from_child(sb, button)
                    if card is not None:
                        cards.append(card)
                except Exception:
                    continue
        except Exception:
            pass

    # ===== 原有逻辑：Expire 标签 =====
    if not cards:
        anchor_xpath = (
            '//*[self::div or self::span or self::p or self::label or self::small or self::strong or self::td]'
            '[normalize-space(.) = "Expire dans" '
            'or normalize-space(.) = "Expires in" '
            'or normalize-space(.) = "Expire le" '
            'or normalize-space(.) = "Expires on" '
            'or normalize-space(.) = "到期" '
            'or normalize-space(.) = "过期"]'
        )
        try:
            anchors = sb.driver.find_elements(By.XPATH, anchor_xpath)
        except Exception:
            anchors = []
        for anchor in anchors:
            try:
                card = find_card_container_from_child(sb, anchor)
                if card is not None:
                    cards.append(card)
            except Exception:
                continue

    # ===== 兜底：div 表格行 =====
    if not cards:
        try:
            rows = sb.driver.execute_script('''
                const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
                const leaves = [];
                document.querySelectorAll('*').forEach(el => {
                    if (el.children.length === 0) {
                        const t = (el.textContent || '').trim();
                        if (UUID_RE.test(t)) leaves.push(el);
                    }
                });
                const seen = new Set();
                const rows = [];
                for (const n of leaves) {
                    let cur = n;
                    let row = null;
                    for (let i = 0; i < 15 && cur; i++, cur = cur.parentElement) {
                        if (!cur.parentElement) break;
                        const text = (cur.innerText || '').trim();
                        if (text.length > 800) continue;
                        if (/\\d/.test(text) && text.split('\\n').filter(s => s.trim()).length >= 3) {
                            row = cur;
                            break;
                        }
                    }
                    if (!row) continue;
                    const um = (row.innerText || '').match(UUID_RE);
                    if (!um) continue;
                    const uuid = um[0].toLowerCase();
                    if (seen.has(uuid)) continue;
                    seen.add(uuid);
                    row.setAttribute('data-acl-uuid', uuid);
                    rows.push(row);
                }
                return rows;
            ''')
        except Exception as e:
            print(f"表格行回退解析失败: {e}")
            rows = []

        if rows:
            print(f"表格回退：找到 {len(rows)} 行")

        for row in rows:
            try:
                uuid = (row.get_attribute('data-acl-uuid') or '').lower()
            except Exception:
                uuid = ''

            # 缓存命中：跳过点击
            if uuid and uuid in _NAME_CACHE and uuid in _RENEWAL_NOTE_CACHE:
                print(f"  ✅ 从缓存读取: {_NAME_CACHE[uuid]} (uuid={uuid[:8]})")
                cards.append(row)
                continue

            # 1) 点击 Actif 展开
            status_els = []
            try:
                status_els = row.find_elements(
                    By.XPATH,
                    './/*[self::span or self::button or self::div or self::a]'
                    '[normalize-space(.) = "Actif" '
                    'or normalize-space(.) = "Active" '
                    'or normalize-space(.) = "Activé" '
                    'or normalize-space(.) = "Activated" '
                    'or normalize-space(.) = "活跃" '
                    'or normalize-space(.) = "正常"]'
                )
            except Exception:
                status_els = []

            chosen = None
            for el in status_els:
                try:
                    if el.is_displayed() and el.is_enabled():
                        chosen = el
                        break
                except Exception:
                    continue

            if chosen is not None:
                try:
                    safe_click_element(sb, chosen, "Actif 展开")
                    print("  ✅ 已点击 Actif 展开")
                    # 增加等待时间，让下拉内容完全渲染
                    sb.sleep(2.5)
                except Exception as e:
                    print(f"  点击 Actif 失败: {e}")
            else:
                btns = []
                try:
                    btns = row.find_elements(
                        By.XPATH,
                        './/*[@aria-controls and starts-with(@aria-controls, "service-details-")] | '
                        './/button[contains(@aria-label, "Details")] | '
                        './/*[starts-with(@aria-label, "Details : ")]'
                    )
                except Exception:
                    btns = []
                if btns:
                    try:
                        safe_click_element(sb, btns[0], "Details 展开")
                        print("  ✅ 已点击 Details 展开")
                        sb.sleep(2.5)
                    except Exception as e:
                        print(f"  点击 Details 失败: {e}")

            # 2) 读项目名
            name = ''
            try:
                name = sb.driver.execute_script('''
                    const names = [];
                    document.querySelectorAll('span').forEach(s => {
                        const t = (s.textContent || '');
                        if (!/personnalis|Custom name|自定义名称/i.test(t)) return;
                        if (t.length > 300) return;
                        const strong = s.querySelector('strong');
                        if (strong) {
                            const v = strong.textContent.trim();
                            if (v && v.length <= 80 && !names.includes(v)) names.push(v);
                        }
                    });
                    return names.length ? names[0] : '';
                ''') or ''
            except Exception as e:
                print(f"  读取 Nom personnalisé 失败: {e}")

            # 3) ★ 读续期提示：全局搜索，并加入智能兜底 ★
            renewal_note = ''
            try:
                # 先尝试从全局直接读取
                renewal_note = sb.driver.execute_script('''
                    const candidates = [];
                    document.querySelectorAll('span, div, p, small, strong, li').forEach(el => {
                        const t = (el.textContent || '').trim();
                        if (t.length > 250) return;
                        if (/Renewal\\s+will\\s+be\\s+available|Renouvellement[^\\n]*disponible|可续期/i.test(t)) {
                            candidates.push(t);
                        }
                    });
                    // 返回最短的那个（通常是纯净的提示文本）
                    candidates.sort((a, b) => a.length - b.length);
                    return candidates.length ? candidates[0] : '';
                ''') or ''
            except Exception as e:
                print(f"  读取续期提示失败: {e}")

            # ★ 智能兜底：如果页面没显示提示，根据过期时间生成
            if not renewal_note:
                expiry = get_project_expiry(row)
                if expiry and expiry != '未知':
                    # 尝试从 "3j 17h" 或 "09/29/2026" 推断
                    m_days = re.match(r'(\d+)\s*j', expiry)
                    if m_days:
                        days_left = int(m_days.group(1))
                        if days_left <= 2:
                            renewal_note = 'Renewal is available now'
                        else:
                            renewal_note = f'Renewal will be available in {days_left - 2} days'
                    else:
                        renewal_note = 'Renewal will be available 2 days before expiration'

            # 4) 写入缓存
            if name:
                if uuid:
                    _NAME_CACHE[uuid] = name
                print(f"  ✅ 项目名: {name} (uuid={uuid[:8] or '-'})")
            else:
                print(f"  ⚠️ 未能读到项目名 (uuid={uuid[:8] or '-'})")

            if renewal_note:
                if uuid:
                    _RENEWAL_NOTE_CACHE[uuid] = renewal_note
                print(f"  📅 续期提示: {renewal_note}")
            else:
                print(f"  ⚠️ 未能读到续期提示 (uuid={uuid[:8] or '-'})")

            cards.append(row)

    # ===== 兜底诊断 =====
    if not cards:
        try:
            mon_dump = sb.driver.execute_script('''
                const hits = [];
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
                let tn;
                while (tn = walker.nextNode()) {
                    const s = (tn.textContent || '').trim();
                    if (s.includes('Mon VPS') || s.includes('Nom personnalisé') || s.includes('Renewal will be available')) {
                        let p = tn.parentElement;
                        if (p) {
                            const h = (p.outerHTML || '').slice(0, 1500);
                            if (!hits.includes(h)) hits.push(h);
                        }
                    }
                }
                return hits.slice(0, 5);
            ''')
            if mon_dump:
                print("=" * 60)
                print("🔍 找到相关元素：")
                for i, h in enumerate(mon_dump, 1):
                    print(f"---- 元素 #{i} ----")
                    print(h)
                print("=" * 60)
        except Exception as e:
            print(f"诊断失败: {e}")

    return dedupe_project_cards(cards)
