"""OpenAI兼容供应商的薄适配；配置只含环境变量名，不保存凭据。"""
from dataclasses import dataclass, asdict
import os
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    model: str
    base_url: str
    credential_env: str

    def snapshot(self):
        return asdict(self)

    def image(self, data_url):
        value = {'url': data_url}
        if self.name == 'deepseek': value['detail'] = 'original'
        return {'type': 'image_url', 'image_url': value}

    def parameters(self, thinking='enabled', effort='high'):
        if self.name == 'kimi':
            if thinking != 'enabled': raise ValueError('Kimi思考模型必须启用thinking')
            if self.model.startswith('kimi-k3.1-'):
                return {'max_completion_tokens': 32768 if effort == 'max' else 16384, 'reasoning_effort': effort}
            return {'max_tokens': 32768, 'extra_body': {'thinking': {'type': 'enabled'}}}
        if self.name == 'qwen':
            return {'max_tokens': 16384 if thinking == 'enabled' else 6000,
                    'extra_body': {'enable_thinking': thinking == 'enabled', 'vl_high_resolution_images': True}}
        return {'max_tokens': (32768 if effort == 'max' else 16384) if thinking == 'enabled' else 6000,
                'extra_body': {'thinking': {'type': thinking}},
                **({'reasoning_effort': effort} if thinking == 'enabled' else {})}


@dataclass(frozen=True)
class OpenAIProviderConfig(ProviderConfig):
    token_limit_field: str = 'max_completion_tokens'
    max_output_tokens: int = 6000

    def parameters(self, thinking='enabled', effort='high'):
        # 通用入口只发送标准字段，推理及供应商私有配置由面试官的服务决定。
        return {self.token_limit_field: self.max_output_tokens}


PROVIDERS = {
    'deepseek': ProviderConfig('deepseek', 'deepseek-flash', 'https://api.deepseek.com', 'DEEPSEEK_API_KEY'),
    'qwen': ProviderConfig('qwen', 'qwen3.8-max-0902', 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'DASHSCOPE_API_KEY'),
    'kimi': ProviderConfig('kimi', 'kimi-k2.7-code', 'https://api.moonshot.cn/v1', 'MOONSHOT_API_KEY'),
    'openai': OpenAIProviderConfig('openai', '', 'https://api.openai.com/v1', 'OPENAI_API_KEY'),
}


def get_provider(name='deepseek', model=None, base_url=None):
    default = PROVIDERS[name]
    if name == 'openai':
        selected_model = model or os.environ.get('OPENAI_MODEL', '')
        if not selected_model.strip(): raise ValueError('通用OpenAI入口需要--model或OPENAI_MODEL')
        endpoint = base_url or os.environ.get('OPENAI_BASE_URL') or default.base_url
        address = urlsplit(endpoint)
        if address.scheme not in ('http', 'https') or not address.hostname:
            raise ValueError('OPENAI_BASE_URL必须为有效的HTTP或HTTPS接口地址')
        if address.username or address.password or address.query or address.fragment:
            raise ValueError('接口地址不能包含用户名、密码、查询参数或片段；凭据只由OPENAI_API_KEY提供')
        token_field = os.environ.get('OPENAI_TOKEN_LIMIT_FIELD', 'max_completion_tokens')
        if token_field not in ('max_completion_tokens', 'max_tokens'):
            raise ValueError('OPENAI_TOKEN_LIMIT_FIELD只能为max_completion_tokens或max_tokens')
        try: token_limit = int(os.environ.get('OPENAI_MAX_OUTPUT_TOKENS', '6000'))
        except ValueError: raise ValueError('OPENAI_MAX_OUTPUT_TOKENS必须为1至32768的整数') from None
        if not 1 <= token_limit <= 32768: raise ValueError('OPENAI_MAX_OUTPUT_TOKENS必须为1至32768的整数')
        return OpenAIProviderConfig(name, selected_model.strip(), endpoint, default.credential_env, token_field, token_limit)
    return ProviderConfig(name, model or default.model, base_url or default.base_url, default.credential_env)
