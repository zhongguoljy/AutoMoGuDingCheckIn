import json
import logging
import os
import tempfile
import time

import requests
from requests.exceptions import RequestException
from PIL import Image

from util.Tool import create_sign, aes_encrypt, aes_decrypt, get_current_month_info

# 常量
BASE_URL = 'https://api.moguding.net:9000/'
HEADERS = {
    'user-agent': 'Dart/2.17 (dart:io)',
    'content-type': 'application/json; charset=utf-8',
    'accept-encoding': 'gzip',
    'host': 'api.moguding.net:9000'
}

logging.basicConfig(
    format='[%(asctime)s] %(name)s %(levelname)s: %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %I:%M:%S'
)
logger = logging.getLogger('ApiModule')


class ApiClient:
    """
    ApiClient类用于与远程服务器进行交互，包括用户登录、获取实习计划、获取打卡信息、提交打卡等功能。
    该类主要通过POST请求与API进行通信，并支持自动处理Token失效的情况。

    Attributes:
        config_manager (ConfigManager): 用于管理配置的实例。
        max_retries (int): 控制请求失败后重新尝试的次数，默认值为1。
    """

    def __init__(self, config_manager):
        """
        初始化ApiClient实例。

        :param config_manager: 用于管理配置的实例。
        :type config_manager: ConfigManager
        """
        self.config_manager = config_manager
        self.max_retries = 3  # 控制重新尝试的次数

    def _post_request(self, url, headers, data, msg='请求失败', retry_count=0):
        """
        发送POST请求，并处理请求过程中可能发生的错误。
        包括自动重试机制和Token失效处理。

        :param url: 请求的API地址（不包括BASE_URL部分）。
        :type url: str
        :param headers: 请求头信息，包括授权信息。
        :type headers: dict
        :param data: POST请求的数据。
        :type data: dict
        :param msg: 如果请求失败，输出的错误信息前缀，默认为'请求失败'。
        :type msg: str, optional
        :param retry_count: 当前请求的重试次数，默认为0。
        :type retry_count: int, optional

        :return: 如果请求成功，返回响应的JSON数据。
        :rtype: dict

        :raises ValueError: 如果请求失败或响应包含错误信息，则抛出包含详细错误信息的异常。
        """
        try:
            response = requests.post(f'{BASE_URL}{url}', headers=headers, json=data, timeout=10)
            response.raise_for_status()
            rsp = response.json()

            if rsp.get('code') == 200:
                return rsp

            error_msg = rsp.get('msg', '未知错误')
            if 'token失效' in error_msg and retry_count < self.max_retries:
                wait_time = 0.3 * (2 ** retry_count)
                logger.warning(f"Token失效: 重试 {retry_count + 1}/{self.max_retries}，等待 {wait_time:.2f} 秒")
                time.sleep(wait_time)
                logger.warning('Token失效，正在重新登录...')
                self.login()
                headers['authorization'] = self.config_manager.get_user_info('token')
                return self._post_request(url, headers, data, msg, retry_count + 1)
            else:
                raise ValueError(error_msg)

        except (requests.RequestException, ValueError) as e:
            if retry_count >= self.max_retries:
                logger.error(f'{msg}: {e}')
                raise ValueError(f'{msg}: {str(e)}')

            wait_time = 0.3 * (2 ** retry_count)
            logger.warning(f"{msg}: 重试 {retry_count + 1}/{self.max_retries}，等待 {wait_time:.2f} 秒")
            time.sleep(wait_time)

        return self._post_request(url, headers, data, msg, retry_count + 1)

    def login(self):
        """
        执行用户登录操作，获取新的用户信息并更新配置。

        此方法使用已加密的用户凭据发送登录请求，并在成功后更新用户信息。

        :raises ValueError: 如果登录请求失败，抛出包含详细错误信息的异常。
        """
        url = 'session/user/v5/login'
        data = {
            'phone': aes_encrypt(self.config_manager.get_config('phone')),
            'password': aes_encrypt(self.config_manager.get_config('password')),
            'captcha': None,
            'loginType': 'android',
            'uuid': '',
            'device': 'android',
            'version': '5.14.0',
            't': aes_encrypt(str(int(time.time() * 1000)))
        }
        rsp = self._post_request(url, HEADERS, data, '登陆失败')
        user_info = json.loads(aes_decrypt(rsp.get('data', '')))
        self.config_manager.update_config('userInfo', user_info)

    def fetch_internship_plan(self):
        """
        获取当前用户的实习计划并更新配置中的planInfo。

        该方法会发送请求获取当前用户的实习计划列表，并将结果更新到配置管理器中。

        :raises ValueError: 如果获取实习计划失败，抛出包含详细错误信息的异常。
        """
        url = 'practice/plan/v3/getPlanByStu'
        data = {
            "pageSize": 999999,
            "t": aes_encrypt(str(int(time.time() * 1000)))
        }
        headers = self._get_authenticated_headers(
            sign_data=[
                self.config_manager.get_user_info('userId'),
                self.config_manager.get_user_info('roleKey')
            ]
        )
        rsp = self._post_request(url, headers, data, '获取planID失败')
        plan_info = rsp.get('data', [{}])[0]
        self.config_manager.update_config('planInfo', plan_info)

    def get_job_info(self):
        """
        获取用户的工作id。

        该方法会发送请求获取当前用户的岗位id。

        :return: 用户的工作id。
        :rtype: dict

        :raises ValueError: 如果获取岗位信息失败，抛出包含详细错误信息的异常。
        """
        url = 'practice/job/v4/infoByStu'
        data = {
            "planId": self.config_manager.get_plan_info('planId'),
            "t": aes_encrypt(str(int(time.time() * 1000)))
        }
        headers = self._get_authenticated_headers()
        rsp = self._post_request(url, headers, data, '获取岗位信息失败')
        return rsp.get('data', {})

    def get_submitted_reports_info(self, report_type):
        """
        获取已经提交的日报、周报或月报的数量。

        :param report_type: 报告类型，可选值为 "day"（日报）、"week"（周报）或 "month"（月报）。
        :type report_type: str
        :return: 已经提交的报告数量。
        :rtype: dict
        :raises ValueError: 如果获取数量失败，抛出包含详细错误信息的异常。
        """
        url = 'practice/paper/v2/listByStu'
        data = {
            "currPage": 1,
            "pageSize": 10,
            "reportType": report_type,
            "planId": self.config_manager.get_plan_info('planId'),
            "t": aes_encrypt(str(int(time.time() * 1000)))
        }
        headers = self._get_authenticated_headers(
            sign_data=[
                self.config_manager.get_user_info('userId'),
                self.config_manager.get_user_info('roleKey'),
                report_type
            ]
        )
        rsp = self._post_request(url, headers, data, '获取报告列表失败')

        return rsp

    def submit_report(self, report_info):
        """
        提交报告。

        :param report_info: 报告信息。
        :type report_info: dict
        :return: 无
        :rtype: None
        :raises ValueError: 如果提交报告失败，抛出包含详细错误信息的异常。
        """
        url = 'practice/paper/v5/save'
        headers = self._get_authenticated_headers(
            sign_data=[
                self.config_manager.get_user_info('userId'),
                report_info.get('reportType'),
                self.config_manager.get_plan_info('planId'),
                report_info.get('title'),
            ]
        )
        data = {
            "address": None,
            "applyId": None,
            "applyName": None,
            "attachmentList": None,
            "commentNum": None,
            "commentContent": None,
            "content": report_info.get('content'),
            "createBy": None,
            "createTime": None,
            "depName": None,
            "reject": None,
            "endTime": report_info.get('endTime', None),
            "headImg": None,
            "yearmonth": report_info.get('yearmonth', None),
            "imageList": None,
            "isFine": None,
            "latitude": None,
            "gpmsSchoolYear": None,
            "longitude": None,
            "planId": self.config_manager.get_plan_info('planId'),
            "planName": None,
            "reportId": None,
            "reportType": report_info.get('reportType'),
            "reportTime": report_info.get('reportTime', None),
            "isOnTime": None,
            "schoolId": None,
            "startTime": report_info.get('startTime', None),
            "state": None,
            "studentId": None,
            "studentNumber": None,
            "supportNum": None,
            "title": report_info.get('title'),
            "url": None,
            "username": None,
            "weeks": report_info.get('weeks', None),
            "videoUrl": None,
            "videoTitle": None,
            "attachments": report_info.get('attachments', ''),
            "companyName": None,
            "jobName": None,
            "jobId": report_info.get('jobId', ''),
            "score": None,
            "tpJobId": None,
            "starNum": None,
            "confirmDays": None,
            "isApply": None,
            "compStarNum": None,
            "compScore": None,
            "compComment": None,
            "compState": None,
            "apply": None,
            "levelEntity": None,
            "t": aes_encrypt(str(int(time.time() * 1000)))
        }
        self._post_request(url, headers, data, report_info.get('msg'))

    def get_weeks_date(self):
        """
        获取本周周报周期信息

        :return: 包含周报周报周期信息的字典。
        :rtype: dict
        """
        url = 'practice/paper/v3/getWeeks1'
        data = {
            "t": aes_encrypt(str(int(time.time() * 1000)))
        }
        headers = self._get_authenticated_headers()
        rsp = self._post_request(url, headers, data, '获取周报周期失败')
        return rsp.get('data', [])[0]

    def get_checkin_info(self):
        """
        获取用户的打卡信息。

        该方法会发送请求获取当前用户当月的打卡记录。

        :return: 包含用户打卡信息的字典。
        :rtype: dict

        :raises ValueError: 如果获取打卡信息失败，抛出包含详细错误信息的异常。
        """
        url = 'attendence/clock/v2/listSynchro'
        headers = self._get_authenticated_headers()
        data = {
            **get_current_month_info(),
            "t": aes_encrypt(str(int(time.time() * 1000)))
        }
        rsp = self._post_request(url, headers, data, '获取打卡信息失败')
        # 每月第一天的第一次打卡返回的是空，所以特殊处理返回空字典
        return rsp.get('data', [{}])[0] if rsp.get('data') else {}

    def submit_clock_in(self, checkin_info):
        """
        提交打卡信息。

        该方法会根据传入的打卡信息生成打卡请求，并发送至服务器完成打卡操作。

        :param checkin_info: 包含打卡类型及相关信息的字典。
        :type checkin_info: dict

        :raises ValueError: 如果打卡提交失败，抛出包含详细错误信息的异常。
        """
        url = 'attendence/clock/v4/save'
        logger.info(f'打卡类型：{checkin_info.get("type")}')

        data = {
            "distance": None,
            "address": self.config_manager.get_config('address'),
            "content": None,
            "lastAddress": None,
            "lastDetailAddress": checkin_info.get('lastDetailAddress'),
            "attendanceId": None,
            "city": self.config_manager.get_config('city'),
            "area": self.config_manager.get_config('area'),
            "country": "中国",
            "createBy": None,
            "createTime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "description": None,
            "device": self.config_manager.get_config('device'),
            "images": None,
            "isDeleted": None,
            "isReplace": None,
            "latitude": self.config_manager.get_config('latitude'),
            "longitude": self.config_manager.get_config('longitude'),
            "modifiedBy": None,
            "modifiedTime": None,
            "province": self.config_manager.get_config('province'),
            "schoolId": None,
            "state": "NORMAL",
            "teacherId": None,
            "teacherNumber": None,
            "type": checkin_info.get('type'),
            "stuId": None,
            "planId": self.config_manager.get_plan_info('planId'),
            "attendanceType": None,
            "username": None,
            "attachments": None,
            "userId": self.config_manager.get_user_info('userId'),
            "isSYN": None,
            "studentId": None,
            "applyState": None,
            "studentNumber": None,
            "memberNumber": None,
            "headImg": None,
            "attendenceTime": None,
            "depName": None,
            "majorName": None,
            "className": None,
            "logDtoList": None,
            "isBeyondFence": None,
            "practiceAddress": None,
            "tpJobId": None,
            "t": aes_encrypt(str(int(time.time() * 1000)))
        }

        headers = self._get_authenticated_headers(
            sign_data=[
                self.config_manager.get_config('device'),
                checkin_info.get('type'),
                self.config_manager.get_plan_info('planId'),
                self.config_manager.get_user_info('userId'),
                self.config_manager.get_config('address')
            ]
        )

        self._post_request(url, headers, data, '打卡失败')

    def get_upload_token(self):
        """
        获取上传文件的认证令牌。

        该方法会发送请求获取上传文件的认证令牌。

        :return: 上传文件的认证令牌。
        :rtype: str
        """
        url = 'session/upload/v1/token'
        headers = self._get_authenticated_headers()
        data = {
            "t": aes_encrypt(str(int(time.time() * 1000)))
        }
        rsp = self._post_request(url, headers, data, '获取上传文件的认证令牌失败')
        return rsp.get('data', '')

    def _get_authenticated_headers(self, sign_data=None):
        """
        生成带有认证信息的请求头。

        该方法会从配置管理器中获取用户的Token、用户ID及角色Key，并生成包含这些信息的请求头。
        如果提供了sign_data，还会生成并添加签名信息。

        :param sign_data: 用于生成签名的数据列表，默认为None。
        :type sign_data: list, optional

        :return: 包含认证信息和签名的请求头字典。
        :rtype: dict
        """
        headers = {
            **HEADERS,
            'authorization': self.config_manager.get_user_info('token'),
            'userid': self.config_manager.get_user_info('userId'),
            'rolekey': self.config_manager.get_user_info('roleKey'),
        }
        if sign_data:
            headers['sign'] = create_sign(*sign_data)
        return headers


def generate_article(config, tittle, job_info, count=500, max_retries=3, retry_delay=1):
    headers = {
        'Authorization': f"Bearer {config.get_config('apikey')}",
    }

    data = {
        "model": config.get_config('model'),
        "messages": [
            {"role": "system",
             "content": f"According to the information provided by the user, write an article according to the template, the reply does not allow the use of Markdown syntax, the content is in line with the job description, the content of the article is fluent, in line with the Chinese grammatical conventions,Number of characters greater than {count}"},
            {"role": "system",
             "content": "模板：实习地点：xxxx\n\n工作内容：\n\nxzzzx\n\n工作总结：\n\nxxxxxx\n\n遇到问题：\n\nxzzzx\n\n自我评价：\n\nxxxxxx"},
            {"role": "user",
             "content": f"{tittle},工作地点:{job_info['jobAddress']};公司名:{job_info['practiceCompanyEntity']['companyName']};"
                        f"岗位职责:{job_info['quartersIntroduce']};公司所属行业:{job_info['practiceCompanyEntity']['tradeValue']}"}
        ]
    }

    url = f"{config.get_config('apiUrl').rstrip('/')}/v1/chat/completions"

    for attempt in range(max_retries):
        try:
            logger.info(f"第 {attempt + 1} 次尝试生成文章")
            response = requests.post(url=url, headers=headers, json=data, timeout=30)
            response.raise_for_status()
            logger.info("文章生成成功")
            return response.json()['choices'][0]['message']['content']
        except RequestException as e:
            logger.warning(f"第 {attempt + 1} 次尝试失败: {str(e)}")
            if attempt == max_retries - 1:
                logger.error(f"达到最大重试次数。最后一次错误: {str(e)}")
                raise ValueError(f"达到最大重试次数。最后一次错误: {str(e)}")
            time.sleep(retry_delay)
        except (KeyError, IndexError) as e:
            logger.error(f"解析响应时出错: {str(e)}")
            raise ValueError(f"解析响应时出错: {str(e)}")
        except Exception as e:
            logger.error(f"发生意外错误: {str(e)}")
            raise ValueError(f"发生意外错误: {str(e)}")


def upload(token, images, config, max_retries=3, retry_delay=1):
    """
    上传图片（支持一次性上传多张图片）

    :param token: 上传文件的认证令牌
    :type token: str
    :param images: 图片路径列表
    :type images: list
    :param config: 配置
    :type config: ConfigManager
    :param max_retries: 最大重试次数
    :type max_retries: int
    :param retry_delay: 重试延迟（秒）
    :type retry_delay: int
    :return: 成功上传的图片key，用逗号分隔
    :rtype: str
    """
    url = 'https://up.qiniup.com/'
    headers = {
        'host': 'up.qiniup.com',
        'accept-encoding': 'gzip',
        'user-agent': 'Dart / 2.17(dart:io)'
    }

    successful_keys = []

    for image_path in images:
        for attempt in range(max_retries):
            try:
                # 使用临时文件处理图片
                with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_file:
                    # 打开并转换图片为JPG格式
                    with Image.open(image_path) as img:
                        # 如果图片大于1MB，进行压缩
                        if os.path.getsize(image_path) > 1_000_000:
                            img = img.convert('RGB')
                            img.save(temp_file.name, 'JPEG', quality=70, optimize=True)
                        else:
                            img = img.convert('RGB')
                            img.save(temp_file.name, 'JPEG')

                    # 读取处理后的图片内容
                    with open(temp_file.name, 'rb') as f:
                        key = (
                            f"upload/{config.get_user_info('orgJson').get('snowFlakeId', '')}"
                            f"/{time.strftime('%Y-%m-%d', time.localtime())}"
                            f"/report/{config.get_user_info('userId')}_{int(time.time() * 1000000)}.jpg"
                        )
                        data = {
                            'token': token,
                            'key': key,
                            'x-qn-meta-fname': f'{int(time.time() * 1000)}.jpg'
                        }

                        files = {
                            'file': (key, f, 'application/octet-stream')
                        }
                        response = requests.post(url, headers=headers, files=files, data=data)
                        response.raise_for_status()  # 如果响应状态不是200，将引发HTTPError异常

                        # 检查响应中是否包含key字段
                        response_data = response.json()
                        print(response_data)
                        if 'key' in response_data:
                            successful_keys.append(response_data['key'])
                        else:
                            logging.warning(f"上传成功但响应中没有key字段: {image_path}")

                # 如果成功上传，跳出重试循环
                break

            except requests.exceptions.RequestException as e:
                logging.error(f"上传失败 (尝试 {attempt + 1}/{max_retries}): {str(e)}")
                if attempt == max_retries - 1:
                    logging.error(f"上传失败，已达到最大重试次数: {image_path}")
                    raise ValueError(f"上传失败，已达到最大重试次数: {image_path}")
                else:
                    time.sleep(retry_delay)

            except Exception as e:
                logging.error(f"处理图片时发生错误: {str(e)}")
                raise ValueError(f"处理图片时发生错误: {str(e)}")

            finally:
                # 删除临时文件
                if os.path.exists(temp_file.name):
                    os.unlink(temp_file.name)

    # 返回成功上传的图片key，用逗号分隔
    return ','.join(successful_keys)
