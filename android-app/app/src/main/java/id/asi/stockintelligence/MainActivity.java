package id.asi.stockintelligence;

import android.annotation.SuppressLint;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.OnBackPressedCallback;
import androidx.appcompat.app.AppCompatActivity;

public class MainActivity extends AppCompatActivity {
    private static final String HOME_URL = "https://ai-stock-intelligence-zijcl2pi2kjweeappbkjiy.streamlit.app";
    private WebView webView;
    private ProgressBar progress;
    private TextView status;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildUi();
        configureWebView();
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack();
                } else {
                    finish();
                }
            }
        });
        if (savedInstanceState == null) {
            webView.loadUrl(HOME_URL);
        } else {
            webView.restoreState(savedInstanceState);
        }
    }

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setLayoutParams(new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.MATCH_PARENT));

        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        int p = dp(8);
        top.setPadding(p, p / 2, p, p / 2);

        status = new TextView(this);
        status.setText("AI Stock Intelligence");
        status.setTextSize(14f);
        status.setSingleLine(true);
        top.addView(status, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));

        Button home = new Button(this);
        home.setText("Home");
        home.setAllCaps(false);
        home.setOnClickListener(v -> webView.loadUrl(HOME_URL));
        top.addView(home, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT));

        Button refresh = new Button(this);
        refresh.setText("Refresh");
        refresh.setAllCaps(false);
        refresh.setOnClickListener(v -> webView.reload());
        top.addView(refresh, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT));

        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        progress.setVisibility(View.GONE);

        webView = new WebView(this);
        webView.setLayoutParams(new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));

        root.addView(top);
        root.addView(progress, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(3)));
        root.addView(webView);
        setContentView(root);
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setLoadsImagesAutomatically(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(true);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setSupportZoom(false);
        settings.setUserAgentString(settings.getUserAgentString() + " AIStockIntelligenceAPK/1.8.53");

        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(webView, true);

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                progress.setProgress(newProgress);
                progress.setVisibility(newProgress < 100 ? View.VISIBLE : View.GONE);
            }
        });

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                status.setText("Memuat...");
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                status.setText("AI Stock Intelligence");
                CookieManager.getInstance().flush();
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String scheme = uri.getScheme();
                if ("https".equalsIgnoreCase(scheme) || "http".equalsIgnoreCase(scheme)) {
                    return false;
                }
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    status.setText("Koneksi bermasalah");
                    Toast.makeText(MainActivity.this, "Tidak dapat memuat dashboard. Periksa internet lalu tekan Refresh.", Toast.LENGTH_LONG).show();
                }
            }

            @Override
            public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
                Toast.makeText(MainActivity.this, "WebView dimulai ulang.", Toast.LENGTH_SHORT).show();
                recreate();
                return true;
            }
        });
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        webView.saveState(outState);
        super.onSaveInstanceState(outState);
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.stopLoading();
            webView.setWebChromeClient(null);
            webView.setWebViewClient(null);
            webView.destroy();
        }
        super.onDestroy();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
