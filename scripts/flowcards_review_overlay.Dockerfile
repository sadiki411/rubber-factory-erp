# Verification fallback when Docker Hub is unreachable. NOT production deployment.
# Caller tags the already-inspected immutable production base image under this
# isolated name, then builds with no production data, ports, or mounted secrets.
FROM erp-flow-review-base:baced3ad
USER root
COPY review-deps/ /review-deps/
RUN dpkg -i /review-deps/fonts-wqy-microhei_0.2.0-beta-3.1_all.deb && pip install --no-cache-dir --no-index --no-deps /review-deps/reportlab-4.4.5-py3-none-any.whl && rm -rf /review-deps
COPY backend/ /app/backend/
WORKDIR /app/backend
