"""
OZON Seller API 客户端
封装所有 OZON API 调用，处理认证、错误、重试、日志

API 文档参考：https://docs.ozon.ru/api/seller/
"""

import json
import time
import requests
import datetime


# ── 基础配置 ──────────────────────────────────────────

OZON_API_BASE = "https://api-seller.ozon.ru"
REQUEST_TIMEOUT = 30  # 秒
MAX_RETRIES = 2       # 可重试错误的最大重试次数
RETRY_DELAY = 2       # 重试间隔（秒）

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


# ── 自定义异常 ────────────────────────────────────────

class OzonAPIError(Exception):
    """OZON API 通用错误"""
    def __init__(self, message, status_code=None, response_body=None, endpoint=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.endpoint = endpoint


class OzonAuthError(OzonAPIError):
    """401/403 — 认证/权限错误"""
    pass


class OzonRateLimitError(OzonAPIError):
    """429 — 请求频率限制"""
    pass


class OzonServerError(OzonAPIError):
    """5xx — 服务器错误"""
    pass


class OzonValidationError(OzonAPIError):
    """OZON 返回的业务校验错误（如必填属性缺失）"""
    def __init__(self, message, status_code=None, response_body=None, endpoint=None, errors=None):
        super().__init__(message, status_code, response_body, endpoint)
        self.errors = errors or []


# ── API 客户端 ────────────────────────────────────────

class OzonAPIClient:
    """OZON Seller API 客户端实例"""

    def __init__(self, client_id, api_key):
        """
        参数:
            client_id: OZON API Client-Id
            api_key: OZON API Key
        """
        self.client_id = client_id
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Client-Id": self.client_id,
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
        })

    # ── 底层请求方法 ─────────────────────────────────

    def _request(self, method, path, body=None, retry_on=None):
        """
        发送 API 请求并处理错误。

        返回: (response_dict, status_code)
        异常: OzonAPIError 及其子类
        """
        url = f"{OZON_API_BASE}{path}"
        start_time = time.time()

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self.session.request(
                    method=method,
                    url=url,
                    json=body,
                    timeout=REQUEST_TIMEOUT,
                )
                elapsed = time.time() - start_time

                # 2xx — 成功
                if 200 <= resp.status_code < 300:
                    result = resp.json() if resp.text else {}
                    return result, resp.status_code, elapsed

                # 非 2xx — 分类处理
                error_msg = self._extract_error_message(resp)
                resp_body = resp.text[:2000]  # 截断长响应

                if resp.status_code in (401, 403):
                    raise OzonAuthError(
                        f"认证失败 (HTTP {resp.status_code}): {error_msg}",
                        status_code=resp.status_code,
                        response_body=resp_body,
                        endpoint=path,
                    )
                elif resp.status_code == 429:
                    if attempt < MAX_RETRIES:
                        time.sleep(RETRY_DELAY * (attempt + 1))
                        continue
                    raise OzonRateLimitError(
                        f"请求频率限制 (HTTP 429): {error_msg}",
                        status_code=429,
                        response_body=resp_body,
                        endpoint=path,
                    )
                elif resp.status_code >= 500:
                    if attempt < MAX_RETRIES:
                        time.sleep(RETRY_DELAY * (attempt + 1))
                        continue
                    raise OzonServerError(
                        f"OZON 服务器错误 (HTTP {resp.status_code}): {error_msg}",
                        status_code=resp.status_code,
                        response_body=resp_body,
                        endpoint=path,
                    )
                else:
                    # 4xx 客户端错误（非 401/403/429）
                    raise OzonValidationError(
                        f"请求错误 (HTTP {resp.status_code}): {error_msg}",
                        status_code=resp.status_code,
                        response_body=resp_body,
                        endpoint=path,
                        errors=self._extract_validation_errors(resp),
                    )

            except requests.exceptions.Timeout:
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                    continue
                raise OzonAPIError(f"请求超时 ({REQUEST_TIMEOUT}s): {path}")
            except requests.exceptions.ConnectionError as e:
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                    continue
                raise OzonAPIError(f"连接失败: {e}")
            except OzonAPIError:
                raise  # 已处理的异常直接抛出
            except Exception as e:
                raise OzonAPIError(f"未知错误: {e}")

    def _extract_error_message(self, resp):
        """从响应体中提取可读的错误消息"""
        try:
            body = resp.json()
            if isinstance(body, dict):
                # 尝试多种常见的 OZON 错误字段
                for key in ('message', 'error', 'details', 'errorMessage'):
                    val = body.get(key)
                    if val:
                        if isinstance(val, list):
                            return '; '.join(str(v) for v in val[:5])
                        return str(val)[:500]
            return str(body)[:500]
        except Exception:
            return resp.text[:500]

    def _extract_validation_errors(self, resp):
        """提取业务校验错误列表"""
        errors = []
        try:
            body = resp.json()
            if isinstance(body, dict):
                details = body.get('details', [])
                for d in details:
                    if isinstance(d, dict):
                        errors.append({
                            'field': d.get('attribute_name', d.get('field', '')),
                            'message': d.get('message', ''),
                            'code': d.get('code', ''),
                        })
        except Exception:
            pass
        return errors

    # ── 业务 API 方法 ────────────────────────────────

    # ── 4.1 连通性测试 / 商品列表 ─────────────────

    def test_connectivity(self):
        """
        连通性测试 — 查询商品列表（只取 1 条）

        对应接口: POST /v3/product/list

        返回: True（成功） / 抛出异常（失败）
        """
        result, status, elapsed = self._request("POST", "/v3/product/list", body={
            "filter": {},
            "last_id": "",
            "limit": 1,
        })
        return {
            "success": True,
            "elapsed": elapsed,
            "has_items": len(result.get("result", {}).get("items", [])) > 0,
            "total": result.get("result", {}).get("total", 0),
        }

    def list_products(self, last_id="", limit=100, filter_dict=None):
        """
        查询商品列表

        对应接口: POST /v3/product/list

        参数:
            last_id: 分页游标
            limit: 每页数量（最大 1000）
            filter_dict: 筛选条件

        返回: (items, total, last_id)
        """
        body = {
            "filter": filter_dict or {},
            "last_id": last_id,
            "limit": min(limit, 1000),
        }
        result, _, _ = self._request("POST", "/v3/product/list", body)
        data = result.get("result", {})
        return data.get("items", []), data.get("total", 0), data.get("last_id", "")

    # ── 4.3 类目树 ──────────────────────────────────

    def get_category_tree(self, language="DEFAULT"):
        """
        获取 OZON 类目树

        对应接口: POST /v1/description-category/tree

        返回（归一化后）: [{"category_id": ..., "title": ..., "children": [...]}]
        """
        body = {"language": language}
        result, _, _ = self._request("POST", "/v1/description-category/tree", body)
        raw = result.get("result", result)  # 有的版本不包 result
        if isinstance(raw, list):
            return self._normalize_category_tree(raw)
        return []

    @staticmethod
    def _normalize_category_tree(nodes):
        """归一化类目树：统一 category_id / title 字段名"""
        out = []
        for n in nodes:
            cat_id = n.get("category_id") or n.get("description_category_id")
            title = n.get("title") or n.get("category_name") or ""
            children = n.get("children", [])
            normalized = {
                "category_id": str(cat_id) if cat_id else "",
                "title": title,
            }
            if children:
                normalized["children"] = OzonAPIClient._normalize_category_tree(children)
            out.append(normalized)
        return out

    def get_category_tree_with_subtree(self, category_id, language="DEFAULT"):
        """
        获取指定类目的子类目树（包含 type_id 层级）

        对应接口: POST /v1/description-category/tree + category_id
        返回: [{"category_id": ..., "title": ..., "type_id": ..., "type_name": ..., "children": [...]}]
        """
        body = {"language": language, "category_id": int(category_id)}
        result, _, _ = self._request("POST", "/v1/description-category/tree", body)
        raw = result.get("result", result)
        if not isinstance(raw, list):
            return []
        return self._normalize_subtree(raw)

    @staticmethod
    def _normalize_subtree(nodes):
        """归一化子树：保留 type_id / type_name，兼容多种字段名"""
        out = []
        for n in nodes:
            cat_id = n.get("category_id") or n.get("description_category_id")
            title = n.get("title") or n.get("category_name") or ""
            type_id = n.get("type_id")
            type_name = n.get("type_name", "")
            children = n.get("children", [])
            normalized = {
                "category_id": str(cat_id) if cat_id else "",
                "title": title,
            }
            if type_id:
                normalized["type_id"] = str(type_id)
                normalized["type_name"] = type_name
            if children:
                normalized["children"] = OzonAPIClient._normalize_subtree(children)
            out.append(normalized)
        return out

    def get_attribute_values(self, description_category_id, type_id, attribute_id, last_value_id=0, limit=5000):
        """
        获取属性字典值

        对应接口: POST /v1/description-category/attribute/values

        返回: [{"id": ..., "value": ..., "info": ...}]
        """
        body = {
            "description_category_id": int(description_category_id),
            "type_id": int(type_id),
            "attribute_id": int(attribute_id),
            "last_value_id": last_value_id,
            "limit": min(limit, 5000),
        }
        result, _, _ = self._request("POST", "/v1/description-category/attribute/values", body)
        return result.get("result", [])

    # ── 4.4 类目属性 ────────────────────────────────

    def get_category_types(self, description_category_id, language="DEFAULT"):
        """
        【已废弃，请用 get_category_types_for_node】
        获取指定 description_category_id 下的 type 列表。
        保留以兼容旧代码。
        """
        result = self.get_category_types_for_node(description_category_id, language)
        return result.get("types", [])

    def get_category_types_for_node(self, description_category_id, language="DEFAULT"):
        """
        获取指定类目**直接**关联的 type 列表。

        核心逻辑：
        - 遍历 OZON 返回的子树，记录每个 type 的直接父级 description_category_id
        - 只把 parent_dcid == 当前请求的 description_category_id 的 type 视为 direct_types
        - total_in_tree 只作为提示信息，不作为当前类目是否过宽的判断依据

        对应接口: POST /v1/description-category/tree + category_id

        返回:
        {
            "types": [direct types],
            "direct_count": N,
            "total_in_tree": M,
            "child_category_count": N,
            "has_children": bool,
            "category_too_broad": bool
        }
        """
        body = {"language": language, "category_id": int(description_category_id)}
        result, _, _ = self._request("POST", "/v1/description-category/tree", body)
        tree = result.get("result", [])

        direct_types = []
        total_types = 0
        child_category_count = 0

        def walk(nodes, parent_dcid):
            nonlocal total_types, child_category_count

            for n in nodes:
                node_dcid = str(n.get("description_category_id") or n.get("category_id") or "")
                type_id = n.get("type_id")
                type_name = n.get("type_name", "")

                if type_id:
                    total_types += 1
                    if str(parent_dcid) == str(description_category_id):
                        direct_types.append({
                            "type_id": str(type_id),
                            "type_name": type_name,
                            "description_category_id": str(description_category_id)
                        })

                if node_dcid and not type_id:
                    if str(parent_dcid) == str(description_category_id):
                        child_category_count += 1

                children = n.get("children", [])
                if children:
                    walk(children, node_dcid or parent_dcid)

        walk(tree, description_category_id)

        return {
            "types": direct_types,
            "direct_count": len(direct_types),
            "total_in_tree": total_types,
            "child_category_count": child_category_count,
            "has_children": child_category_count > 0,
            "category_too_broad": len(direct_types) > 300
        }

    def estimate_type_count(self, description_category_id, language="DEFAULT"):
        """
        快速估算类目下的 type 数量（直接 + 后代），不存储任何数据。
        返回: {"direct_count": N, "total_count": M, "too_broad": bool, "has_children": bool}
        """
        result = self.get_category_types_for_node(description_category_id, language)
        return {
            "direct_count": result["direct_count"],
            "total_count": result["total_in_tree"],
            "too_broad": result["direct_count"] > 100,
            "has_children": result["has_children"],
        }

    def get_category_attributes(self, description_category_id, type_id, attribute_type="ALL", language="DEFAULT"):
        """
        获取指定类目的属性字典（必须提供 description_category_id + type_id）。

        对应接口: POST /v1/description-category/attribute

        参数:
            description_category_id: 类目 ID（来自类目树倒数第二层）
            type_id: 商品类型 ID（来自类目树叶子层）
        抛出: OzonAPIError（若缺少 type_id）
        """
        if not type_id:
            raise OzonAPIError(
                "缺少 type_id。get_category_attributes 必须同时传入 description_category_id 和 type_id。",
                endpoint="/v1/description-category/attribute",
            )

        body = {
            "description_category_id": int(description_category_id),
            "type_id": int(type_id),
            "language": language,
        }
        result, _, _ = self._request("POST", "/v1/description-category/attribute", body)
        attrs = result.get("result", [])
        return self._normalize_attributes(attrs, attribute_type)

    @staticmethod
    def _normalize_attributes(attrs, attribute_type="ALL"):
        """归一化属性列表字段名：id→attribute_id, is_required → bool"""
        out = []
        for a in attrs:
            norm = {
                "attribute_id": a.get("id") or a.get("attribute_id", ""),
                "name": a.get("name", ""),
                "description": a.get("description", ""),
                "is_required": bool(a.get("is_required") or a.get("required", False)),
                "is_collection": bool(a.get("is_collection") or a.get("is_aspect", False)),
                "is_dictionary": bool(a.get("dictionary_id")),
                "dictionary_id": a.get("dictionary_id"),
                "data_type": str(a.get("type", "string")),
                "unit": a.get("unit") or None,
                "group_name": a.get("group_name") or a.get("category_department_name", ""),
                "max_value_count": a.get("max_value_count") or a.get("max_value", 1),
            }
            if attribute_type == "REQUIRED" and not norm["is_required"]:
                continue
            out.append(norm)
        return out

    # ── 4.4 创建/更新商品 ────────────────────────────

    def import_product(self, product_data):
        """
        创建或更新商品

        对应接口: POST /v3/product/import

        参数:
            product_data: 商品数据字典，包含:
                - offer_id (str): 本地商品标识（必填）
                - name (str): 俄语标题（必填）
                - category_id (int): OZON 类目 ID（必填）
                - price (str): 售价 RUB
                - vat (str): 增值税，默认 "0"
                - barcode (str): 条码
                - description (str): 描述
                - attributes (list): 属性列表
                - images (list): 图片 URL 列表
                - skus (list): 多 SKU 数据

        返回: {"task_id": ..., "status": "created"}
        """
        # OZON 要求某些字段必须是字符串
        body = self._sanitize_product_data(product_data)
        result, _, _ = self._request("POST", "/v3/product/import", body)
        return result.get("result", result)

    def import_product_info(self, task_id):
        """
        查询商品导入任务状态

        对应接口: POST /v1/product/import/info

        返回: {"status": ..., "items": [...]}
        """
        result, _, _ = self._request("POST", "/v1/product/import/info", body={
            "task_id": int(task_id),
        })
        return result.get("result", result)

    # ── 4.5 图片上传 ────────────────────────────────

    def upload_image(self, image_url, primary=True):
        """
        上传商品图片（通过 URL）

        对应接口: POST /v1/product/pictures/import

        参数:
            image_url: 图片 URL
            primary: 是否为主图
        """
        raise NotImplementedError("图片上传接口待 OZON 文档确认后实现")

    # ── 4.6 价格更新 ────────────────────────────────

    def update_prices(self, prices):
        """
        更新商品价格

        对应接口: POST /v1/product/prices/update（待实测确认）

        参数:
            prices: [{"offer_id": ..., "price": ..., "min_price": ...}, ...]
        """
        raise NotImplementedError("价格更新接口待 OZON 文档确认后实现")

    # ── 4.6 库存更新 ────────────────────────────────

    def update_stocks(self, stocks):
        """
        更新商品库存

        对应接口: POST /v2/products/stocks（待实测确认）

        参数:
            stocks: [{"offer_id": ..., "stock": ..., "warehouse_id": ...}, ...]
        """
        raise NotImplementedError("库存更新接口待 OZON 文档确认后实现")

    # ── 辅助方法 ─────────────────────────────────────

    # ── 4.7 在线商品管理 ───────────────────────────────

    def get_product_info(self, offer_ids=None, product_ids=None, skus=None):
        """
        获取在线商品详细信息

        对应接口: POST /v3/product/info/list

        返回: [{"id": ..., "offer_id": ..., "name": ..., ...}]
        """
        body = {}
        if offer_ids:
            body["offer_id"] = list(offer_ids) if not isinstance(offer_ids, list) else offer_ids
        if product_ids:
            body["product_id"] = list(product_ids) if not isinstance(product_ids, list) else product_ids
        if skus is not None:
            body["sku"] = list(skus) if not isinstance(skus, list) else skus
        result, _, _ = self._request("POST", "/v3/product/info/list", body)
        return result.get("result", result).get("items", [])

    def archive_products(self, product_ids):
        """
        归档商品

        对应接口: POST /v1/product/archive

        参数:
            product_ids: 商品 ID 列表
        返回: {"result": true}
        """
        body = {"product_id": product_ids if isinstance(product_ids, list) else [product_ids]}
        result, _, _ = self._request("POST", "/v1/product/archive", body)
        return result

    def unarchive_products(self, product_ids):
        """
        取消归档商品

        对应接口: POST /v1/product/unarchive

        参数:
            product_ids: 商品 ID 列表
        返回: {"result": true}
        """
        body = {"product_id": product_ids if isinstance(product_ids, list) else [product_ids]}
        result, _, _ = self._request("POST", "/v1/product/unarchive", body)
        return result

    def _sanitize_product_data(self, data):
        """清理商品数据，确保格式符合 OZON API 要求"""
        clean = dict(data)

        # 确保关键字段是字符串
        if 'offer_id' in clean:
            clean['offer_id'] = str(clean['offer_id'])
        if 'price' in clean and clean['price'] is not None:
            clean['price'] = str(clean['price'])
        if 'vat' not in clean:
            clean['vat'] = "0"
        if 'old_price' in clean and clean['old_price'] is not None:
            clean['old_price'] = str(clean['old_price'])
        if 'premium_price' in clean and clean['premium_price'] is not None:
            clean['premium_price'] = str(clean['premium_price'])
        if 'barcode' in clean and clean['barcode'] is not None:
            clean['barcode'] = str(clean['barcode'])

        # 处理 SKU
        if 'skus' in clean:
            clean['skus'] = [
                self._sanitize_product_data(sku) for sku in clean['skus']
            ]

        # 处理图片 — OZON 期望对象数组
        if 'images' in clean:
            clean['images'] = [
                self._normalize_image(img) for img in clean['images']
            ]

        return clean

    def _normalize_image(self, img):
        """将图片输入统一为 OZON API 格式"""
        if isinstance(img, str):
            return {"file_name": "", "link": img}
        if isinstance(img, dict):
            return {
                "file_name": img.get("file_name", ""),
                "link": img.get("link", img.get("url", "")),
            }
        return img


# ── 客户端工厂函数 ────────────────────────────────────

def create_client(account):
    """
    从 OzonAccount 模型实例创建 API 客户端。

    参数:
        account: OzonAccount 实例

    返回: OzonAPIClient 实例
    """
    return OzonAPIClient(
        client_id=account.client_id,
        api_key=account.api_key,
    )


def test_account(account):
    """
    测试店铺连通性，并将结果写回 account 记录。

    参数:
        account: OzonAccount 实例

    返回: (success: bool, message: str)
    """
    try:
        client = create_client(account)
        result = client.test_connectivity()
        now = datetime.datetime.now()

        account.sync_status = 'ok'
        account.last_sync_at = now
        account.sync_error = None
        account.updated_at = now
        account.save()

        return True, f"连接成功（{result['elapsed']:.1f}s，共 {result['total']} 件商品）"
    except OzonAuthError as e:
        _record_sync_error(account, f"认证失败: {e}")
        return False, f"认证失败 — 请检查 Client-Id / Api-Key"
    except OzonRateLimitError as e:
        _record_sync_error(account, f"请求频率限制: {e}")
        return False, "请求过于频繁，请稍后重试"
    except OzonServerError as e:
        _record_sync_error(account, f"OZON 服务器错误: {e}")
        return False, "OZON 服务器暂时不可用，请稍后重试"
    except OzonAPIError as e:
        _record_sync_error(account, f"连接失败: {e}")
        return False, f"连接失败 — {e}"
    except Exception as e:
        _record_sync_error(account, f"未知错误: {e}")
        return False, f"未知错误 — {e}"


def _record_sync_error(account, error_msg):
    """记录同步错误到 account"""
    account.sync_status = 'error'
    account.sync_error = error_msg
    account.last_sync_at = datetime.datetime.now()
    account.updated_at = datetime.datetime.now()
    account.save()
