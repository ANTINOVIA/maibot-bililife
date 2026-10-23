"""回滚到旧配置格式时不扩大自动发布范围。仅解析目标源码字段，不执行旧源码。"""
import ast


def adapt_settings(settings,source):
    tree=ast.parse(source)
    cls=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='Settings')
    allowed={x.target.id for x in cls.body if isinstance(x,ast.AnnAssign) and isinstance(x.target,ast.Name)}
    if 'mode' not in allowed:
        return {k:v for k,v in settings.items() if k in allowed}
    if 'mode' in settings:
        return {k:v for k,v in settings.items() if k in allowed}
    result={k:v for k,v in settings.items() if k in allowed}
    all_automatic=all(settings.get(k) is True for k in ('comments_enabled','replies_enabled','auto_publish_comments','auto_publish_replies'))
    result['mode']='autonomous' if all_automatic else 'observe_only' if not settings.get('comments_enabled',True) and not settings.get('replies_enabled',True) else 'draft_only'
    result['autonomous_limits_confirmed']=all_automatic
    return result
