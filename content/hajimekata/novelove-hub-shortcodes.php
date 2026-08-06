<?php
/**
 * Novelove 始め方ハブ用ショートコード
 * [novelove_hub_ad store="dmm|lovecal|dlsite" genre="bl|tl"]
 * [novelove_hub_samples store="dmm|lovecal|dlsite" genre="bl|tl"]
 */

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
        $cat = $genre . '-' . $media; // bl-manga / bl-novel / bl-voice
        $popular = get_option('novelove_popular_ids');
        if (is_string($popular)) {
            $popular = json_decode($popular, true);
        }
        if (!is_array($popular)) {
            $popular = array();
        }

        // 1) 人気IDから店×カテゴリ一致を探す
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

        // 2) 新しい順で候補を拾う
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
        $thumb = get_the_post_thumbnail(
            $post,
            'thumb160',
            array(
                'class' => 'related-entry-card-thumb-image card-thumb-image wp-post-image',
                'alt'   => $title,
            )
        );
        if (!$thumb) {
            $fifu = get_post_meta($post->ID, 'fifu_image_url', true);
            if ($fifu) {
                $thumb = sprintf(
                    '<img src="%s" class="related-entry-card-thumb-image card-thumb-image wp-post-image" alt="%s" loading="lazy" decoding="async" />',
                    esc_url($fifu),
                    esc_attr($title)
                );
            } else {
                $thumb = '<img src="' . esc_url(get_stylesheet_directory_uri() . '/images/no-image-160.png') . '" class="related-entry-card-thumb-image card-thumb-image wp-post-image" alt="" />';
            }
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

add_shortcode('novelove_hub_ad', function ($atts) {
    $a = shortcode_atts(array(
        'store' => 'dmm',
        'genre' => 'bl',
    ), $atts, 'novelove_hub_ad');
    $store = sanitize_key($a['store']);
    $genre = ($a['genre'] === 'tl') ? 'tl' : 'bl';
    $uid = 'nlv-hub-ad-' . $store . '-' . $genre . '-' . uniqid();

    $dlsite_bl = '<a rel="noopener sponsored nofollow" href="https://dlaf.jp/bl/dlaf/=/aid/novelove/url/https%3A%2F%2Fwww.dlsite.com%2Fbl%2Franking%2F%3Futm_medium%3Daffiliate%26utm_campaign%3Dbnlink%26utm_content%3Dbn_sp_300_250_dojin_01.jpg" target="_blank"><img src="https://www.dlsite.com/img/female/dojin/bn_sp_300_250_dojin_01.jpg" alt="DLsite がるまに" width="300" height="250" border="0" loading="lazy" /></a>';
    $dlsite_tl = '<a rel="noopener sponsored nofollow" href="https://dlaf.jp/girls/dlaf/=/aid/novelove/url/https%3A%2F%2Fwww.dlsite.com%2Fgirls%2Franking%2F%3Futm_medium%3Daffiliate%26utm_campaign%3Dbnlink%26utm_content%3Dbn_sp_300_250_dojin_01.gif" target="_blank"><img src="https://www.dlsite.com/img/female/dojin/bn_sp_300_250_dojin_01.gif" alt="DLsite がるまに" width="300" height="250" border="0" loading="lazy" /></a>';

    // 期限つきキャンペーンは既存サイドバーと同じIDを利用（常設寄りを優先）
    $now = time();
    $limit_2 = strtotime('2026-08-20 23:59:59 Asia/Tokyo');
    $lovecal_bl = ($now < $limit_2) ? '1987_300_250' : '1742_300_250';
    $lovecal_tl = ($now < $limit_2) ? '1988_300_250' : '1732_300_250';

    if ($store === 'dlsite') {
        $html = ($genre === 'tl') ? $dlsite_tl : $dlsite_bl;
        return '<div class="nlv-hub-ad" style="display:flex;justify-content:center;margin:16px 0 8px;">' . $html . '</div>';
    }

    $banner_id = '1827_300_250'; // DMM新規登録70%OFF
    if ($store === 'lovecal') {
        $banner_id = ($genre === 'tl') ? $lovecal_tl : $lovecal_bl;
    }

    $src = 'https://widget-view.dmm.co.jp/js/banner_placement.js?affiliate_id=novelove-001&banner_id=' . rawurlencode($banner_id);
    return '<div class="nlv-hub-ad" id="' . esc_attr($uid) . '" style="display:flex;justify-content:center;width:100%;min-height:250px;margin:16px 0 8px;"><ins class="widget-banner"></ins><script class="widget-banner-script" src="' . esc_url($src) . '"></script></div>';
});

add_shortcode('novelove_hub_samples', function ($atts) {
    $a = shortcode_atts(array(
        'store' => 'dmm',
        'genre' => 'bl',
    ), $atts, 'novelove_hub_samples');
    $store = sanitize_key($a['store']);
    $genre = ($a['genre'] === 'tl') ? 'tl' : 'bl';

    $map = array(
        'manga' => '漫画',
        'novel' => '小説',
        'voice' => 'ボイス',
    );
    $cards = '';
    foreach ($map as $media => $label) {
        $post = novelove_hub_pick_post($store, $genre, $media);
        $cards .= novelove_hub_render_card($post, $label);
    }
    if ($cards === '') {
        return '';
    }
    return '<aside class="related-entries rect-entry-card nlv-hub-samples" style="margin:8px 0 24px;">' . $cards . '</aside>';
});
