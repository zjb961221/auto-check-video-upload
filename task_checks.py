"""Explicit, read-only completion checks; no automatic writes or polling."""
from copy import deepcopy
import json
from api_client import ApiError,json_path


def validate_verification(preset):
    rule=preset.get('verification')
    if rule is None:return
    if not isinstance(rule,dict) or set(rule)-{'request','success','task_id_path','conditions','correlate'}:raise ApiError('verification 配置格式错误')
    request=rule.get('request',{});success=rule.get('success',{})
    if not isinstance(request,dict) or request.get('method','GET') not in ('GET','HEAD') or request.get('confirm',False) or not isinstance(request.get('path'),str):raise ApiError('结果核验仅允许配置 GET/HEAD 查询请求')
    if not isinstance(success,dict) or set(success)!={'path','equals'} or not isinstance(success['path'],str) or not success['path']:raise ApiError('结果核验必须配置最终成功字段 path / equals')
    conditions=rule.get('conditions',[])
    if not isinstance(conditions,list) or len(conditions)>10 or any(not isinstance(c,dict) or set(c)!={'path','equals'} or not isinstance(c['path'],str) for c in conditions):raise ApiError('核验 conditions 必须为 path / equals 条件数组')
    correlate=rule.get('correlate',{})
    if not isinstance(correlate,dict) or any(not isinstance(k,str) or not k or not isinstance(v,str) or not v for k,v in correlate.items()):raise ApiError('correlate 必须为核验字段路径到触发响应字段路径的映射')
    if 'task_id_path' in rule and not isinstance(rule['task_id_path'],str):raise ApiError('task_id_path 必须为文本')


def verification_params(preset,params,response):
    values=deepcopy(params)
    path=preset['verification'].get('task_id_path')
    if path:
        try:values['task_id']=json_path(json.loads(response.text()),path)
        except (ValueError,TypeError):raise ApiError('触发响应中缺少 task_id，必须人工核实任务，不得重复触发') from None
        if values['task_id'] is None or isinstance(values['task_id'],(dict,list)):raise ApiError('响应中的 task_id 无效，请人工核实')
    if preset['verification'].get('correlate'):
        try:body=json.loads(response.text())
        except ValueError:raise ApiError('触发响应不能解析，无法关联本次任务') from None
        expected={}
        for result_path,trigger_path in preset['verification']['correlate'].items():
            expected[result_path]=json_path(body,trigger_path)
            if expected[result_path] is None:raise ApiError('触发响应缺少关联字段，请人工核实本次任务')
        values['_expected']=expected
    return values


def completion(response,rule,params=None):
    if not 200<=response.status<300:return False
    try:value=json_path(json.loads(response.text()),rule['path'])
    except (ValueError,TypeError,ApiError):return False
    return type(value) is type(rule['equals']) and value==rule['equals']


def task_completed(response,verification,params):
    if not completion(response,verification['success']):return False
    if any(not completion(response,c) for c in verification.get('conditions',[])):return False
    if verification.get('correlate'):
        try:body=json.loads(response.text())
        except (ValueError,ApiError):return False
        expected=params.get('_expected',{})
        for path in verification['correlate']:
            try:value=json_path(body,path)
            except ApiError:return False
            if path not in expected or type(value) is not type(expected[path]) or value!=expected[path]:return False
    return True
