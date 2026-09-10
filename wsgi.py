# -*- coding: utf-8 -*-
"""
PythonAnywhere 용 WSGI 진입점.

PythonAnywhere 웹 설정 화면의 "WSGI configuration file" 에 아래 두 줄만 넣어도 되고,
이 파일을 그대로 가리키게 해도 된다.

    import sys
    sys.path.insert(0, '/home/<사용자명>/matman')
    from app import app as application
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app as application  # noqa: E402

if __name__ == "__main__":
    application.run()
