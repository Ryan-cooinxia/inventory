with open('blueprints/ozon.py', 'r', encoding='utf-8') as f:
    content = f.read()

old = '# P1: 系统识别 + 类目树分层过滤'
new = '''# P1: 系统识别 → 本地类目树路径映射 → 路径下推荐type
    if not recommendations and pk:
        rule = CATEGORY_RULES.get(pk, {})
        tkw, confs = rule.get('target_kw',[]), rule.get('conflicts',[])
        skw = rule.get('strong_kw', tkw)
        ACCESSORY_WORDS = ['аксессуар','кнопка','видоискатель','картридж','объектив','адаптер','переходник','чехол','крепление','защит','пленк','фильтр','держател','кронштейн','штатив','крышка','заглушка','ремень','сумка','кейс','насадк','переходн','адаптер','пульт','аккумулятор отдел','зарядн устройств','блок питан','кабель','провод','переходник','вспышк','диффузор','отражатель','софтбокс','направляющ','салазк','площадк','креплени','рукоятк','крышк','линз','фильтр','блютус','bluetooth','пульт','спуск']
        all_confs = list(set(confs + ACCESSORY_WORDS))

        # 本地类目树路径映射：识别结果→从哪里找type
        TREE_PATH_MAP = {
            'action_camera': {
                'preferred_paths': [['электроник','фото','видео'],['электроник','камер'],['электроник','фотоаппарат'],['фото','видео'],['камер','аксессуар']],
                'fallback_note': '本地无运动相机类目,按最接近的摄影摄像配件路径推荐'
            },
            'microphone': {
                'preferred_paths': [['электроник','аудио'],['электроник','микрофон'],['аудио','микрофон'],['аудиотехник']],
                'fallback_note': '本地无麦克风类目,按最接近的音频设备路径推荐'
            },
            'camera': {
                'preferred_paths': [['электроник','фото','видео'],['электроник','камер'],['фото','видео']],
            },
            'headphones': {
                'preferred_paths': [['электроник','аудио'],['аудио','наушник'],['электроник','наушник']],
            },
            'drone': {
                'preferred_paths': [['электроник','квадрокоптер'],['электроник','дрон'],['электроник','игрушк']],
            },
        }

        path_map = TREE_PATH_MAP.get(pk, {})
        preferred = path_map.get('preferred_paths', [[tkw[0]]]) if tkw else []
        fallback_note = path_map.get('fallback_note', '')

        # 1. 在类目树中按preferred_paths逐层匹配
        cat_dcids = []
        matched_path = ''
        for path_words in preferred:
            query = OzonCategory.select().where(OzonCategory.user == current_user)
            first = True
            for w in path_words:
                p = '%' + w + '%'
                if first:
                    query = query.where((OzonCategory.name_cn ** p) | (OzonCategory.name ** p) | (OzonCategory.path ** p))
                    first = False
                else:
                    query = query.where((OzonCategory.name_cn ** p) | (OzonCategory.name ** p) | (OzonCategory.path ** p))
            cats = list(query.limit(30))
            dcids = [c.ozon_category_id for c in cats if c.ozon_category_id]
            if dcids:
                cat_dcids = dcids[:50]
                # 找最佳路径名
                for c in cats:
                    if c.path and (_norm(pk) in _norm(c.path) or any(_norm(k) in _norm(c.name_cn or '')+_norm(c.name or '') for k in skw)):
                        matched_path = c.path or ''
                        break
                if not matched_path and cats:
                    matched_path = cats[0].path or ' > '.join(path_words)
                break

        # 2. 在匹配的类目下找type
        if cat_dcids:
            types = list(OzonCategoryType.select().where(
                (OzonCategoryType.user == current_user) &
                (OzonCategoryType.description_category_id.in_(cat_dcids))
            ).order_by(OzonCategoryType.last_synced_at.desc()))
        else:
            types = []
            if fallback_note: diagnostics.append(fallback_note)
            else: diagnostics.append(f'识别为{pk}({product_info.get("kind_cn","")}),本地类目树无匹配路径')

        # 3. 匹配type+冲突过滤+评分
        matched = []
        for t in types:
            tn = _norm((t.type_name_cn or '') + ' ' + (t.type_name or '') + ' ' + (t.path or ''))
            if any(kw in tn for kw in all_confs): continue
            strong = sum(1 for k in skw if _norm(k) in tn)
            score = sum(1 for k in tkw if _norm(k) in tn) + strong * 3
            if strong > 0 or score >= 2:
                matched.append((t, score, strong > 0))

        matched.sort(key=lambda x: -x[1])
        for t, s, is_strong in matched[:5]:
            rec_name = t.type_name_cn or t.type_name
            path = t.path or matched_path or ''
            cat_node = OzonCategory.get_or_none((OzonCategory.user == current_user) & (OzonCategory.ozon_category_id == t.description_category_id))
            if cat_node and cat_node.path: path = cat_node.path + ' > ' + rec_name
            conf = 0.85 if is_strong else 0.65
            reason = f'识别为{product_info["kind_cn"]},在类目路径{matched_path or "匹配路径"}下匹配到{rec_name}'
            if fallback_note and not is_strong: reason = fallback_note + ': ' + rec_name
            recommendations.append(_make_rec(t.description_category_id,t.type_id,t.type_name or '',t.type_name_cn or '',path,conf,'system_inference' if is_strong else 'tree_fallback',reason,product_info.get('evidence',[])))

        if not recommendations:
            if cat_dcids: diagnostics.append(f'识别为{pk},在{matched_path}下未找到匹配type(配件词已过滤)')
            elif not diagnostics or not any('无匹配' in d for d in diagnostics):
                diagnostics.append(f'识别为{pk}({product_info.get("kind_cn","")}),请同步对应类目树')

'''

content = content.replace(old, new)
with open('blueprints/ozon.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('OK')
