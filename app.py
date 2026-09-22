        </div>

        <script>
            const STOCKS = Array.isArray({json_db}) ? {json_db} : [];
            const inputEl = document.getElementById('unified_search_input');
            const modalEl = document.getElementById('unified_search_modal');
            const listEl = document.getElementById('unified_search_list');
            const footerQueryEl = document.getElementById('unified_search_footer_query');

            function normalizeSearchText(value) {{
                return String(value ?? '')
                    .normalize('NFKC')
                    .toLowerCase()
                    .replace(/\s+/g, '')
                    .replace(/[._\-\/'’(),&]+/g, '');
            }}

            // 검색 인덱스는 normalizeSearchText 정의 이후 생성한다.
            // 이렇게 해야 iframe 초기화 순서와 관계없이 항상 정상적으로 만들어진다.
            const SEARCH_INDEX = STOCKS.map(item => ({
                item: item,
                ticker: normalizeSearchText(item && item.ticker),
                name: normalizeSearchText(item && item.name),
                aliases: (Array.isArray(item && item.aliases) ? item.aliases : []).map(normalizeSearchText)
            }));

            function subsequenceScore(query, text) {{
                if (!query || !text) return 0;
                let qi = 0;
                for (let i = 0; i < text.length && qi < query.length; i++) {{
                    if (text[i] === query[qi]) qi++;
                }}