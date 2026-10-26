package jp.omoti12.tempmonitor;

// ============================================================
// 温湿度モニターのアプリ本体
//   ラズパイの画面（https://raspberrypi.tailfa1dcc.ts.net）を、アプリの中に全画面で表示する。
//   ラズパイはインターネットに公開していないので、スマホの Tailscale が「Connected」のときだけ見られる。
//   つながらないときは、アプリの画面で知らせて「再読み込み」ボタンを出す。
// ============================================================

import android.app.Activity;
import android.app.DownloadManager;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.view.Gravity;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends Activity {

    // ラズパイの画面のアドレス（Tailscale の中だけで開ける）
    static final String SITE_URL = "https://raspberrypi.tailfa1dcc.ts.net/";

    WebView web;            // ラズパイの画面を表示する部品
    LinearLayout errorView; // つながらないときに出す画面
    boolean loadFailed;     // 今の読み込みに失敗したか

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        FrameLayout root = new FrameLayout(this);

        // ---- ラズパイの画面 ----
        web = new WebView(this);
        WebSettings ws = web.getSettings();
        ws.setJavaScriptEnabled(true);      // 画面の表示・更新に必要
        ws.setDomStorageEnabled(true);
        // 確認のダイアログ（「メールを止めます。よろしいですか？」など）を出せるようにする
        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                // ラズパイの画面の中はアプリで開き、ほかのサイトへのリンクはブラウザで開く
                Uri u = req.getUrl();
                if (Uri.parse(SITE_URL).getHost().equals(u.getHost())) return false;
                startActivity(new Intent(Intent.ACTION_VIEW, u));
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                // 画面そのものが読めなかったときだけ、つながらない画面を出す
                if (req.isForMainFrame()) {
                    loadFailed = true;
                    showError(true);
                }
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap icon) {
                loadFailed = false;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (!loadFailed) showError(false);
            }
        });
        // CSVのダウンロード: スマホの「ダウンロード」フォルダに保存する
        web.setDownloadListener((url, userAgent, disposition, mime, length) -> {
            String name = URLUtil.guessFileName(url, disposition, mime);
            DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
            r.addRequestHeader("Cookie", CookieManager.getInstance().getCookie(url));
            r.setTitle(name);
            r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
            ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(r);
            Toast.makeText(this, name + " をダウンロードしています", Toast.LENGTH_SHORT).show();
        });
        root.addView(web);

        // ---- つながらないときの画面 ----
        errorView = new LinearLayout(this);
        errorView.setOrientation(LinearLayout.VERTICAL);
        errorView.setGravity(Gravity.CENTER);
        errorView.setPadding(64, 64, 64, 64);
        errorView.setBackgroundColor(Color.parseColor("#F5F6F8"));
        TextView title = new TextView(this);
        title.setText("ラズパイにつながりません");
        title.setTextSize(20);
        title.setTextColor(Color.parseColor("#1D2330"));
        title.setGravity(Gravity.CENTER);
        TextView msg = new TextView(this);
        msg.setText("\nTailscale のアプリを開いて「Connected」になっているか確認してください。\n"
                + "ラズパイの電源とネットも確認してください。\n");
        msg.setTextSize(15);
        msg.setTextColor(Color.parseColor("#6B7280"));
        msg.setGravity(Gravity.CENTER);
        Button retry = new Button(this);
        retry.setText("再読み込み");
        retry.setOnClickListener(v -> web.loadUrl(SITE_URL));
        errorView.addView(title);
        errorView.addView(msg);
        errorView.addView(retry);
        errorView.setVisibility(View.GONE);
        root.addView(errorView);

        setContentView(root);
        web.loadUrl(SITE_URL);
    }

    void showError(boolean show) {
        errorView.setVisibility(show ? View.VISIBLE : View.GONE);
    }

    @Override
    protected void onResume() {
        super.onResume();
        // つながらない画面のままアプリに戻ってきたら（Tailscale をつないで戻ったときなど）、読み直す
        if (errorView.getVisibility() == View.VISIBLE) web.loadUrl(SITE_URL);
    }

    @Override
    public void onBackPressed() {
        // 戻るボタン: 画面の中で戻れるなら戻る。戻れなければアプリを閉じる
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }
}
