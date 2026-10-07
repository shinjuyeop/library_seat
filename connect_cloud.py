"""The cloud app now authenticates library accounts directly in its web interface."""
import sys

if __name__ == '__main__':
    print('PC 연결 도구가 필요하지 않습니다. 웹에서 도서관 아이디와 비밀번호로 로그인해 주세요.')
    print(sys.argv[1] if len(sys.argv) > 1 else 'https://library-seat-dusky.vercel.app')
