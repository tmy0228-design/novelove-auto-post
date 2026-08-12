<?php
/**
 * Novelove 始め方ハブ用ショートコード
 * [novelove_hub_ad store="dmm|lovecal|dlsite" genre="bl|tl"]
 * [novelove_hub_samples store="dmm|lovecal|dlsite" genre="bl|tl|mix"]
 *
 * genre=mix は店ごとに BL/TL を交互にばらして出す。
 * 本文の <style> は WP に剥がされて生CSSが見えることがあるため、
 * CSS はここから出す（wp_head + ショートコード初回）。
 */

if (!function_exists('novelove_hub_css')) {
    function novelove_hub_css() {
        static $done = false;
        if ($done) {
            return '';
        }
        $done = true;
        return '<style id="nlv-hub-css">'
            . '.nlv-hub{line-height:1.75;color:#333}'
            . '.nlv-hub h2{font-size:1.35em;margin:2em 0 .6em;padding-bottom:.35em;border-bottom:2px solid #f0c0c0}'
            . '.nlv-hub h3{font-size:1.1em;margin:1.2em 0 .4em;color:#c0607f}'
            . '.nlv-hub .nlv-hub-lead{margin:0 0 1.2em}'
            . '.nlv-hub .nlv-hub-store{margin:1.2em 0 .6em;padding:1em 1.1em;background:#fffafb;border:1px solid #f3d0d8;border-radius:12px}'
            . '.nlv-hub .nlv-hub-store ul{margin:.4em 0 0;padding-left:1.2em}'
            . '.nlv-hub .nlv-hub-note{font-size:.85em;color:#888;text-align:center;margin:.2em 0 .8em}'
            . '.nlv-hub .nlv-hub-ad-label{font-size:.8em;color:#999;text-align:center;margin:0 0 .35em}'
            . '.nlv-hub .nlv-hub-media{margin:0 0 1.5em;padding:.8em 1em;background:#fafafa;border-radius:8px;font-size:.95em}'
            . '.nlv-hub .nlv-hub-faq{margin:1.5em 0}'
            . '.nlv-hub .nlv-hub-faq p{margin:0 0 1.1em}'
            . '.nlv-hub .nlv-hub-faq ul{margin:.2em 0 1.1em;padding-left:1.2em}'
            . '.nlv-hub .speech-bubble-left{margin:1em 0 1.4em}'
            . '.nlv-hub .nlv-hub-compare-wrap{overflow-x:auto;margin:0 0 1.6em;-webkit-overflow-scrolling:touch}'
            . '.nlv-hub table.nlv-hub-compare{width:100%;min-width:520px;border-collapse:collapse;font-size:.92em;background:#fff}'
            . '.nlv-hub table.nlv-hub-compare th,.nlv-hub table.nlv-hub-compare td{border:1px solid #f0c0c0;padding:.55em .65em;text-align:center;vertical-align:middle}'
            . '.nlv-hub table.nlv-hub-compare thead th{background:#fff0f4;color:#c0607f;font-weight:700}'
            . '.nlv-hub table.nlv-hub-compare tbody th{background:#fffafb;text-align:left;white-space:nowrap;font-weight:700}'
            . '.nlv-hub .nlv-hub-next{margin:1.2em 0 1.8em;padding:1em 1.1em;background:#fffafb;border:1px solid #f3d0d8;border-radius:12px}'
            . '.nlv-hub .nlv-hub-next p{margin:0 0 .8em}'
            . '.nlv-hub .nlv-hub-next-links{display:flex;flex-wrap:wrap;gap:.55em;list-style:none;margin:0;padding:0}'
            . '.nlv-hub .nlv-hub-next-links a{display:inline-block;padding:.55em .9em;border:1px solid #f0c0c0;border-radius:999px;background:#fff;color:#c0607f!important;text-decoration:none!important;font-weight:700;font-size:.92em}'
            . '.nlv-hub .nlv-hub-next-links a:hover{background:#fff0f4}'
            . '.nlv-hub-samples .nlv-hub-card{display:block;margin:0 0 .8em}'
            . '.nlv-hub-samples .related-entry-card-thumb img{display:block!important;width:160px;height:auto;max-width:100%}'
            /* ハブ本文の DMM widget とサイドバー常設枠の衝突を避ける */
            . 'body.page-nlv-hub #dmm-random-ad-container,body.page-nlv-hub #dmm-random-tv-ad-container,'
            . 'body.page-hajimekata #dmm-random-ad-container,body.page-hajimekata #dmm-random-tv-ad-container,'
            . 'body.page-id-33566 #dmm-random-ad-container,body.page-id-33566 #dmm-random-tv-ad-container{display:none!important}'
            . '</style>';
    }
}

if (!function_exists('novelove_hub_is_hajimekata_page')) {
    function novelove_hub_is_hajimekata_page() {
        return is_page(array('hajimekata'))
            || is_page(33566);
    }
}

if (!function_exists('novelove_hub_is_store_guide_page')) {
    function novelove_hub_is_store_guide_page() {
        return is_page(array('lovecal-guide', 'dlsite-guide', 'dmmbooks-guide'));
    }
}

if (!function_exists('novelove_hub_is_hub_page')) {
    function novelove_hub_is_hub_page() {
        return novelove_hub_is_hajimekata_page() || novelove_hub_is_store_guide_page();
    }
}

add_filter('body_class', function ($classes) {
    if (novelove_hub_is_hajimekata_page()) {
        $classes[] = 'page-hajimekata';
    }
    if (novelove_hub_is_hub_page()) {
        $classes[] = 'page-nlv-hub';
    }
    return $classes;
});

// ハブ本文で DMM widget を出すため、同ページのサイドバー常設枠は無効化（衝突で空白化するため）
add_filter('pre_do_shortcode_tag', function ($return, $tag) {
    if (!novelove_hub_is_hub_page()) {
        return $return;
    }
    if (in_array($tag, array('novelove_random_dmm', 'novelove_random_dmm_tv'), true)) {
        return '';
    }
    return $return;
}, 10, 2);

add_action('wp_head', function () {
    if (!novelove_hub_is_hub_page()) {
        return;
    }
    echo novelove_hub_css() . "\n";
}, 40);

/**
 * FAQPage構造化データ。
 * 質問・回答は content/hajimekata/hajimekata.html の「登録の前によくある質問」と
 * 手動で同期すること（本文を変更したらここも直す）。
 */
if (!function_exists('novelove_hub_faq_jsonld')) {
    function novelove_hub_faq_jsonld() {
        $qa = array(
            array(
                'q' => '登録は無料ですか？すぐお金がかかりますか？',
                'a' => 'どのお店もアカウント作成は無料です。お金がかかるのは作品を買うときだけ。まずは登録だけでもOKです。',
            ),
            array(
                'q' => '登録にクレジットカードは必要ですか？',
                'a' => 'いいえ、3店ともクレジットカードは必須ではありません。メールアドレス（またはSNSアカウント）とパスワードがあれば登録できます。購入時もポイント購入やコンビニ決済など、カード以外の支払い方法を選べます。',
            ),
            array(
                'q' => '無料や試し読みは、どこまで見られますか？',
                'a' => 'お店や作品によってまちまちです。試し読み・サンプル・期間限定の無料公開など、形式はいろいろ。すべてが無料で読めるわけではないので、気になった作品のページで確認してみてください。',
            ),
            array(
                'q' => '漫画・小説・ボイスって何が違いますか？',
                'a' => 'お店によって扱っている形式が違います。DMMブックスは漫画と小説がメイン（ボイスは扱いなし）。らぶカルとDLsiteなら漫画・小説に加えてボイス作品もあります。',
            ),
            array(
                'q' => 'お店ごとの細かい違いも知りたいです',
                'a' => 'それぞれのお店だけをまとめたページもあります。DMMブックス／らぶカル／DLsiteのページで確認してください。',
            ),
            array(
                'q' => 'スマホだけで読めますか？聴けますか？',
                'a' => '主要なお店はスマホのブラウザやアプリで使えます。細かい対応機種はそれぞれの公式サイトでチェックしてみてください。',
            ),
            array(
                'q' => '年齢確認はどうすればいいですか？',
                'a' => '生年月日の入力や画面の案内に沿って進めれば、基本的にそれだけで完了します。身分証の提出が必要になるのは、一部の決済方法を選んだときなど限られたケースです。',
            ),
        );
        $entities = array();
        foreach ($qa as $item) {
            $entities[] = array(
                '@type'          => 'Question',
                'name'           => $item['q'],
                'acceptedAnswer' => array(
                    '@type' => 'Answer',
                    'text'  => $item['a'],
                ),
            );
        }
        $json_ld = array(
            '@context'   => 'https://schema.org',
            '@type'      => 'FAQPage',
            'mainEntity' => $entities,
        );
        return '<script type="application/ld+json">'
            . json_encode($json_ld, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)
            . '</script>';
    }
}

add_action('wp_head', function () {
    if (!novelove_hub_is_hajimekata_page()) {
        return;
    }
    echo novelove_hub_faq_jsonld() . "\n";
}, 41);

if (!function_exists('novelove_hub_store_match')) {
    function novelove_hub_store_match($post_name, $store) {
        $n = strtolower((string) $post_name);
        if ($store === 'dlsite') {
            return (bool) preg_match('/^(rj|bj|vj)/', $n);
        }
        if ($store === 'lovecal') {
            return (bool) preg_match('/^d_/', $n);
        }
        if ($store === 'dmm') {
            return $n !== ''
                && !preg_match('/^(rj|bj|vj)/', $n)
                && !preg_match('/^d_/', $n)
                && !preg_match('/^(bl|tl)-/', $n)
                && strpos($n, 'ranking') === false
                && strpos($n, 'curation') === false
                && strpos($n, 'hajimekata') === false;
        }
        return false;
    }
}

if (!function_exists('novelove_hub_pick_post')) {
    function novelove_hub_pick_post($store, $genre, $media) {
        $genre = ($genre === 'tl') ? 'tl' : 'bl';
        // ボイスだけスラッグが voice-bl / voice-tl（SPECIFICATIONS.md）
        if ($media === 'voice') {
            $cat = 'voice-' . $genre;
        } else {
            $cat = $genre . '-' . $media; // bl-manga / bl-novel
        }
        $popular = get_option('novelove_popular_ids');
        if (is_string($popular)) {
            $popular = json_decode($popular, true);
        }
        if (!is_array($popular)) {
            $popular = array();
        }

        foreach ($popular as $pid) {
            $pid = (int) $pid;
            $p = get_post($pid);
            if (!$p || $p->post_status !== 'publish') {
                continue;
            }
            if (!novelove_hub_store_match($p->post_name, $store)) {
                continue;
            }
            if (!has_term($cat, 'category', $pid)) {
                continue;
            }
            return $p;
        }

        $q = new WP_Query(array(
            'post_type'              => 'post',
            'post_status'            => 'publish',
            'posts_per_page'         => 40,
            'category_name'          => $cat,
            'orderby'                => 'date',
            'order'                  => 'DESC',
            'no_found_rows'          => true,
            'update_post_meta_cache' => true,
            'update_post_term_cache' => false,
        ));
        $found = null;
        if ($q->have_posts()) {
            foreach ($q->posts as $p) {
                if (novelove_hub_store_match($p->post_name, $store)) {
                    $found = $p;
                    break;
                }
            }
        }
        wp_reset_postdata();
        return $found;
    }
}

if (!function_exists('novelove_hub_render_card')) {
    function novelove_hub_render_card($post, $label) {
        if (!$post) {
            return '';
        }
        $url = get_permalink($post);
        $title = get_the_title($post);
        // skip-lazy: 固定ページ上で EWWW の lazyload が発火せず display:none のまま残る対策
        $thumb = get_the_post_thumbnail(
            $post,
            'thumb160',
            array(
                'class'    => 'related-entry-card-thumb-image card-thumb-image wp-post-image skip-lazy',
                'alt'      => $title,
                'loading'  => 'eager',
                'decoding' => 'async',
            )
        );
        if (!$thumb) {
            $fifu = get_post_meta($post->ID, 'fifu_image_url', true);
            if ($fifu) {
                $thumb = sprintf(
                    '<img src="%s" class="related-entry-card-thumb-image card-thumb-image wp-post-image skip-lazy" alt="%s" loading="eager" decoding="async" />',
                    esc_url($fifu),
                    esc_attr($title)
                );
            } else {
                $thumb = '<img src="' . esc_url(get_stylesheet_directory_uri() . '/images/no-image-160.png') . '" class="related-entry-card-thumb-image card-thumb-image wp-post-image skip-lazy" alt="" loading="eager" decoding="async" />';
            }
        } else {
            // get_the_post_thumbnail 経由でも lazy 化されることがあるので保険
            $thumb = str_replace(' class="', ' class="skip-lazy ', $thumb);
            $thumb = preg_replace('/\sloading=("|\')lazy("|\')/', ' loading="eager"', $thumb);
        }

        ob_start();
        ?>
        <a class="related-entry-card-wrap a-wrap border-element cf nlv-hub-card" href="<?php echo esc_url($url); ?>" title="<?php echo esc_attr($title); ?>" style="text-decoration:none;">
            <article class="related-entry-card e-card cf post has-post-thumbnail">
                <figure class="related-entry-card-thumb card-thumb e-card-thumb">
                    <?php echo $thumb; ?>
                    <span class="cat-label"><?php echo esc_html($label); ?></span>
                </figure>
                <div class="related-entry-card-content card-content e-card-content">
                    <div class="related-entry-card-title card-title e-card-title" style="font-size:15px;font-weight:bold;margin:0;padding:0 0 4px;border:none;background:transparent;color:#333;line-height:1.4;">
                        <?php echo esc_html(wp_html_excerpt($title, 42, '…')); ?>
                    </div>
                </div>
            </article>
        </a>
        <?php
        return ob_get_clean();
    }
}

/**
 * DMM widget バナーをサイドバーと同じ方式で順次注入する。
 * - esc_url 済みの data-banner-src は &#038; 化で壊れやすいので data-banner-id 方式
 * - shortcode 内 <script> は環境によって落ちるので wp_footer に出す
 * - サイドバーの novelove_random_dmm と同時だと衝突するため、hajimekata ではサイドバー枠をCSSで隠す
 */
if (!function_exists('novelove_hub_queue_dmm_banner')) {
    function novelove_hub_queue_dmm_banner($box_id, $banner_id, $domain = 'co.jp') {
        if (!isset($GLOBALS['nlv_hub_dmm_banners']) || !is_array($GLOBALS['nlv_hub_dmm_banners'])) {
            $GLOBALS['nlv_hub_dmm_banners'] = array();
        }
        $GLOBALS['nlv_hub_dmm_banners'][] = array(
            'id'     => (string) $box_id,
            'banner' => (string) $banner_id,
            'domain' => (string) $domain,
        );
    }
}

add_action('wp_footer', function () {
    if (empty($GLOBALS['nlv_hub_dmm_banners']) || !is_array($GLOBALS['nlv_hub_dmm_banners'])) {
        return;
    }
    $slots = array();
    foreach ($GLOBALS['nlv_hub_dmm_banners'] as $slot) {
        if (empty($slot['id']) || empty($slot['banner'])) {
            continue;
        }
        $slots[] = array(
            'id'     => $slot['id'],
            'banner' => $slot['banner'],
            'domain' => !empty($slot['domain']) ? $slot['domain'] : 'co.jp',
        );
    }
    if (!$slots) {
        return;
    }
    $json = wp_json_encode($slots);
    echo "<script>(function(){\n"
        . "var slots={$json};\n"
        . "function injectOne(item, done){\n"
        . "  var box=document.getElementById(item.id);\n"
        . "  if(!box){ if(done) done(); return; }\n"
        . "  if(box.getAttribute('data-loaded')){ if(done) done(); return; }\n"
        . "  box.setAttribute('data-loaded','1');\n"
        . "  box.innerHTML='<ins class=\"widget-banner\"></ins>';\n"
        . "  var s=document.createElement('script');\n"
        . "  s.className='widget-banner-script';\n"
        . "  s.src='https://widget-view.dmm.'+item.domain+'/js/banner_placement.js?affiliate_id=novelove-001&banner_id='+encodeURIComponent(item.banner);\n"
        . "  s.onload=function(){ if(done) setTimeout(done, 500); };\n"
        . "  s.onerror=function(){ if(done) setTimeout(done, 200); };\n"
        . "  box.appendChild(s);\n"
        . "}\n"
        . "function run(i){\n"
        . "  if(i>=slots.length) return;\n"
        . "  injectOne(slots[i], function(){ run(i+1); });\n"
        . "}\n"
        . "if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',function(){setTimeout(function(){run(0);},300);});}\n"
        . "else{setTimeout(function(){run(0);},300);}\n"
        . "})();</script>\n";
}, 99);

add_shortcode('novelove_hub_ad', function ($atts) {
    $a = shortcode_atts(array(
        'store' => 'dmm',
        'genre' => 'bl', // bl|tl|random（らぶカル/DLsite向け。DMMは常設1枚なので無視）
    ), $atts, 'novelove_hub_ad');
    $store = sanitize_key($a['store']);
    $genre_raw = strtolower((string) $a['genre']);
    if (in_array($genre_raw, array('random', 'mix', 'any', 'auto'), true)) {
        $genre = (wp_rand(0, 1) === 1) ? 'tl' : 'bl';
    } else {
        $genre = ($genre_raw === 'tl') ? 'tl' : 'bl';
    }
    // 同一ページに複数あってもIDが衝突しないよう genre を含める
    $uid = 'nlv-hub-ad-' . $store . '-' . $genre;

    // サイドバーと同じ素材（functions.php の novelove_random_dmm とIDを揃える）
    $dlsite_bl = '<a rel="noopener sponsored nofollow" href="https://dlaf.jp/bl/dlaf/=/aid/novelove/url/https%3A%2F%2Fwww.dlsite.com%2Fbl%2Franking%2F%3Futm_medium%3Daffiliate%26utm_campaign%3Dbnlink%26utm_content%3Dbn_sp_300_250_dojin_01.jpg" target="_blank"><img src="https://www.dlsite.com/img/female/dojin/bn_sp_300_250_dojin_01.jpg" alt="DLsite がるまに" width="300" height="250" border="0" class="skip-lazy" loading="eager" decoding="async" /></a>';
    $dlsite_tl = '<a rel="noopener sponsored nofollow" href="https://dlaf.jp/girls/dlaf/=/aid/novelove/url/https%3A%2F%2Fwww.dlsite.com%2Fgirls%2Franking%2F%3Futm_medium%3Daffiliate%26utm_campaign%3Dbnlink%26utm_content%3Dbn_sp_300_250_dojin_01.gif" target="_blank"><img src="https://www.dlsite.com/img/female/dojin/bn_sp_300_250_dojin_01.gif" alt="DLsite がるまに" width="300" height="250" border="0" class="skip-lazy" loading="eager" decoding="async" /></a>';

    $now = time();
    $limit_2 = strtotime('2026-08-20 23:59:59 Asia/Tokyo');
    // らぶカル：サイドバー第2期と同じ 75% 系 → 終了後は通常枠
    $lovecal_bl = ($now < $limit_2) ? '1987_300_250' : '1742_300_250';
    $lovecal_tl = ($now < $limit_2) ? '1988_300_250' : '1732_300_250';

    $prefix = novelove_hub_css();
    $label = '<div class="nlv-hub-ad-label">スポンサーリンク（新規登録・キャンペーン）</div>';

    if ($store === 'dlsite') {
        $html = ($genre === 'tl') ? $dlsite_tl : $dlsite_bl;
        return $prefix . $label . '<div class="nlv-hub-ad" style="display:flex;justify-content:center;margin:0 0 12px;">' . $html . '</div>';
    }

    // DMMブックス枠は常設の新規登録クーポン（サイドバー定義と同じ 1827）
    $banner_id = '1827_300_250';
    if ($store === 'lovecal') {
        $banner_id = ($genre === 'tl') ? $lovecal_tl : $lovecal_bl;
    }

    novelove_hub_queue_dmm_banner($uid, $banner_id, 'co.jp');

    return $prefix
        . $label
        . '<div class="nlv-hub-ad" id="' . esc_attr($uid) . '" style="display:flex;justify-content:center;width:100%;min-height:250px;margin:0 0 12px;"></div>';
});

if (!function_exists('novelove_hub_mix_picks')) {
    function novelove_hub_mix_picks($store) {
        // 店ごとに BL/TL と媒体をずらす（少しずつばらす）
        $map = array(
            'dmm'     => array(
                array('bl', 'manga', 'BL・漫画'),
                array('tl', 'novel', 'TL・小説'),
            ),
            'lovecal' => array(
                array('tl', 'manga', 'TL・漫画'),
                array('bl', 'manga', 'BL・漫画'),
            ),
            'dlsite'  => array(
                array('bl', 'novel', 'BL・小説'),
                array('tl', 'manga', 'TL・漫画'),
            ),
        );
        return isset($map[$store]) ? $map[$store] : $map['dmm'];
    }
}

add_shortcode('novelove_hub_samples', function ($atts) {
    $a = shortcode_atts(array(
        'store' => 'dmm',
        'genre' => 'bl',
    ), $atts, 'novelove_hub_samples');
    $store = sanitize_key($a['store']);
    $genre_raw = strtolower((string) $a['genre']);

    $cards = '';
    if ($genre_raw === 'mix') {
        foreach (novelove_hub_mix_picks($store) as $pick) {
            list($genre, $media, $label) = $pick;
            $post = novelove_hub_pick_post($store, $genre, $media);
            $cards .= novelove_hub_render_card($post, $label);
        }
    } else {
        $genre = ($genre_raw === 'tl') ? 'tl' : 'bl';
        $map = array(
            'manga' => '漫画',
            'novel' => '小説',
            'voice' => 'ボイス',
        );
        foreach ($map as $media => $label) {
            $post = novelove_hub_pick_post($store, $genre, $media);
            $cards .= novelove_hub_render_card($post, $label);
        }
    }
    if ($cards === '') {
        return '';
    }
    return novelove_hub_css() . '<aside class="related-entries rect-entry-card nlv-hub-samples" style="margin:8px 0 24px;">' . $cards . '</aside>';
});

/**
 * 紹介記事末尾に「お店の選び方」導線を動的追加（執筆本文には埋め込まない）
 * ランキング／まとめは除外。通常の作品紹介のみ。
 */
add_filter('the_content', function ($content) {
    if (!is_single() || !in_the_loop() || !is_main_query()) {
        return $content;
    }
    $cats = get_the_category();
    if ($cats) {
        foreach ($cats as $cat) {
            $slug = (string) $cat->slug;
            if (strpos($slug, 'ranking') !== false
                || strpos($slug, 'curation') !== false
                || $slug === 'matome') {
                return $content;
            }
        }
    }
    $url = home_url('/hajimekata/');
    $html = '<div class="nlv-hajimekata-cta" style="margin:1.4em 0 0.6em;padding:0.9em 1em;border-top:1px solid #f0e0e4;text-align:center;">'
        . '<a href="' . esc_url($url) . '" style="color:#c0607f;font-weight:700;text-decoration:none;">'
        . 'まだお店が決まっていない方へ → お店の選び方'
        . '</a></div>';
    return $content . $html;
}, 10);