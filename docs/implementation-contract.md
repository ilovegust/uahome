# 첫 버전 구현 계약

기준: ilovegust/uahome main 2d501fb48e1b38da2847e37818620e9f1fc1c3e6. 기존 서비스 코드 없음.

스택: Python 3.12+ 표준 라이브러리 HTTP 서버, SQLite, 빌드 없는 HTML/CSS/JS. 외부 판매/사입 API는 미연결이며 실제 주문·결제·상품 게시·답변 전송을 실행하지 않는다. 사람의 검토를 위한 문서와 상태를 관리한다.

## 공통 데이터

Product: id (서버 UUID), name, category, supplier, source_url, channel, notes, keywords (문자열), selling_points (문자열), specs (문자열), seller_sku/coupang_product_id/fulfillment_center/inbound_reference/tracking_number (문자열), status (candidate/selected/purchase_approved/ordered/received/listing_ready/listed/inbound_planned/in_transit/stocked/paused), quantity (양의 정수), moq (양의 정수), lead_days (0 이상 정수), demand_score/competition_score/supplier_score (0~5; 수동 근거 점수), costs 객체, created_at, updated_at, revision 정수.

costs: sale_price, purchase_price, exchange_rate(default 1), inbound_shipping, outbound_shipping, packaging, fulfillment_fee (그로스 입출고·배송비), storage_fee (예상 보관비), other_cost, customer_shipping, fixed_fee, platform_fee_rate (0~100 퍼센트), payment_fee_rate (0~100), ad_rate (0~100), return_rate (0~100), return_cost, tax_reserve_rate (0~100), target_margin_rate (0~99.99). 금액은 원화, purchase_price만 원 통화이며 exchange_rate로 환산. inbound_shipping은 개당 배분된 금액. 수수료 기준은 sale_price+customer_shipping으로 통일해 명시. 세금은 단순 예비비이며 실제 세액이 아님.

Review: id, product_id, rating (1~5 정수), content, customer_name, channel, external_id, reply_draft, status (pending/drafted/approved/replied), created_at, updated_at, revision.

Approval: id, kind (purchase/listing/review_reply), product_id, review_id(nullable), payload(스냅샷 객체), status (pending/approved/rejected/completed), revision, created_at, updated_at. 승인 후 실행은 사용자가 외부 판매/사입처에서 수동 완료하고 완료 기록. 미연결 상태를 completed=외부 API 성공으로 표시하지 않음.

## 도메인 함수(uahome/domain.py)

ValidationError(ValueError); normalize_product(data:dict)->dict (알려진 키만, default 포함); calculate_margin(costs:dict)->dict; rank_product(product:dict)->dict; build_listing(product:dict)->dict; build_purchase(product:dict)->dict; draft_review_reply(review:dict, product:dict)->dict. margin 키: revenue, landed_cost, total_cost, profit, margin_rate, break_even_price, recommended_price, warnings(list), assumptions(list). rank 키: score, recommendation, reasons(list). listing 키: title, description, keywords(list), missing_fields(list), channel, mode='draft'. purchase 키: supplier, product_name, quantity, estimated_total, currency='KRW', text, warnings. review reply 키: text, category, requires_attention(bool), mode='draft'.

## 저장소(uahome/storage.py)

Store(db_path) SQLite 스키마 초기화. get_state()->dict(products,reviews,approvals,events,connectors). create_product(data)->dict; update_product(id,data,revision)->dict; delete_product(id,revision)->None (연결 리뷰/승인 있으면 거절). create_review(data)->dict; update_review(id,data,revision)->dict; request_approval(kind,product_id,review_id=None)->dict; decide_approval(id,decision,revision)->dict (decision approved/rejected/completed); export_backup()->dict; import_backup(data)->dict (스키마 검증/트랜잭션/기존 데이터 대체, UI 경고). get_state 상품에는 margin, ranking 파생 필드 추가. MissingError(LookupError), ConflictError(ValueError). domain.ValidationError 공통 사용. 이벤트는 실제 변경만 기록. revisions로 오래된 승인과 중복 완료 차단. 미연결 connectors는 명확히 연결 전 표시.

## HTTP API(root 담당 uahome/server.py)

모든 응답 JSON {data:...} 또는 {error:...}. GET /api/state, /api/backup, /api/export/products.csv, /api/export/listings.csv. POST /api/products, PATCH /api/products/:id ({...fields,revision}), DELETE /api/products/:id ({revision}); POST /api/reviews, PATCH /api/reviews/:id ({...fields,revision}); POST /api/calculate ({costs}); POST /api/products/:id/listing; POST /api/products/:id/purchase; POST /api/reviews/:id/draft; POST /api/approvals ({kind,product_id,review_id}); POST /api/approvals/:id/decision ({decision,revision}); POST /api/import ({backup:...}); POST /api/assist (초안·검토요청 일괄 준비, 자동 승인 없음); POST /api/import/calculate (1688 견적 전체 금액 수량별 배분, sourcing.py); POST /api/demo (명시적 예시 데이터 추가, 기존 데이터 보존, 중복 방지).

프론트는 API 연결 오류를 안내. 기본 빈 데이터로 시작. 예시 데이터 불러오기 명시 버튼. 등록 문구/발주서/리뷰 답변 편집·복사·다운로드. 연결 전 실제 게시/자동 결제 버튼 없음. 금액/비율 입력과 검증 오류 표시. 승인함과 기록, 검색/상태 필터, 반응형 레이아웃.

## 담당 경로

- domain 담당: uahome/domain.py, tests/test_domain.py
- storage 담당: uahome/storage.py, tests/test_storage.py
- frontend 담당: uahome/static/index.html, app.js, styles.css
- 총괄: server.py, __main__.py, __init__.py, README, docs, CI, 통합 검사 및 GitHub PR

검증: python -m unittest discover -s tests -v, node --check uahome/static/app.js, HTTP 통합 테스트. 서버는 기본 127.0.0.1에만 바인드. 로컬 운영용 첫 버전이며 공개 배포는 인증 필요. 비밀값과 운영 DB는 GitHub에서 제외.
