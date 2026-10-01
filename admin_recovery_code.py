"""관리자용: 특정 사용자에게 복구 코드를 새로 발급한다.

복구 코드를 받기 전에 가입했고 비밀번호도 잊어버린 사용자를 위한 수단.
발급된 코드를 사용자에게 전달하면, 로그인 화면의 "비밀번호를 잊으셨나요?"에서
직접 새 비밀번호를 설정할 수 있다. 기존 복구 코드는 즉시 무효화된다.

사용법:
    POSTGRESQL_CONNECTION_STRING=... python admin_recovery_code.py <user_code>
"""
import sys

from dotenv import load_dotenv

import db


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    user_code = sys.argv[1]
    load_dotenv()
    db.init_db()
    code = db.issue_recovery_code(user_code)
    if not code:
        print(f"사용자 '{user_code}'를 찾을 수 없습니다.")
        return 1
    print(f"'{user_code}'의 새 복구 코드: {code}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
