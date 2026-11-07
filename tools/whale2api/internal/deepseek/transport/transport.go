package transport

import (
	"context"
	"crypto/tls"
	"fmt"
	"net"
	"net/http"
	"time"

	utls "github.com/refraction-networking/utls"
)

type Doer interface {
	Do(req *http.Request) (*http.Response, error)
}

// deepseekClientHelloID pin cứng profile TLS dùng cho mọi kết nối tới
// chat.deepseek.com (cả đường chính lẫn fallback). Dùng Chrome-on-Android /
// Android WebView làm danh tính TLS để khớp với User-Agent Android; KHÔNG dùng
// _Auto để fingerprint không âm thầm đổi khi nâng utls. Xem QĐ-1/QĐ-4 trong
// kế hoạch Mức 1.
var deepseekClientHelloID = utls.HelloChrome_133

type DialContextFunc func(ctx context.Context, network, addr string) (net.Conn, error)

type Client struct {
	http *http.Client
}

func New(timeout time.Duration) *Client {
	return NewWithDialContext(timeout, nil)
}

func NewWithDialContext(timeout time.Duration, dialContext DialContextFunc) *Client {
	useEnvProxy := dialContext == nil
	if dialContext == nil {
		dialContext = (&net.Dialer{Timeout: 15 * time.Second, KeepAlive: 30 * time.Second}).DialContext
	}
	base := &http.Transport{
		ForceAttemptHTTP2:   false,
		MaxIdleConns:        200,
		MaxIdleConnsPerHost: 100,
		IdleConnTimeout:     90 * time.Second,
		DialContext:         dialContext,
		DialTLSContext:      chromeTLSDialer(dialContext),
		TLSClientConfig:     &tls.Config{MinVersion: tls.VersionTLS12},
	}
	if useEnvProxy {
		base.Proxy = http.ProxyFromEnvironment
	}
	return &Client{http: &http.Client{Timeout: timeout, Transport: base}}
}

func (c *Client) Do(req *http.Request) (*http.Response, error) {
	return c.http.Do(req)
}

func NewFallbackClient(timeout time.Duration, dialContext DialContextFunc) *http.Client {
	useEnvProxy := dialContext == nil
	if dialContext == nil {
		dialContext = (&net.Dialer{Timeout: 15 * time.Second, KeepAlive: 30 * time.Second}).DialContext
	}
	base := &http.Transport{
		ForceAttemptHTTP2:   false,
		MaxIdleConns:        200,
		MaxIdleConnsPerHost: 100,
		IdleConnTimeout:     90 * time.Second,
		DialContext:         dialContext,
		DialTLSContext:      chromeTLSDialer(dialContext),
		TLSClientConfig:     &tls.Config{MinVersion: tls.VersionTLS12},
	}
	if useEnvProxy {
		base.Proxy = http.ProxyFromEnvironment
	}
	return &http.Client{Timeout: timeout, Transport: base}
}

// chromeTLSDialer bắt tay TLS qua uTLS với profile Chrome pin cứng
// (deepseekClientHelloID) và ép ALPN về http/1.1 để thống nhất HTTP version
// giữa đường chính và fallback. Dùng chung cho cả transport chính lẫn
// NewFallbackClient nên không còn đường nào gửi request lên deepseek bằng
// transport mặc định (JA3 Go).
func chromeTLSDialer(dialContext DialContextFunc) func(ctx context.Context, network, addr string) (net.Conn, error) {
	if dialContext == nil {
		dialContext = (&net.Dialer{Timeout: 15 * time.Second, KeepAlive: 30 * time.Second}).DialContext
	}
	return func(ctx context.Context, network, addr string) (net.Conn, error) {
		plainConn, err := dialContext(ctx, network, addr)
		if err != nil {
			return nil, err
		}
		host, _, _ := net.SplitHostPort(addr)
		uCfg := &utls.Config{ServerName: host}
		uConn := utls.UClient(plainConn, uCfg, deepseekClientHelloID)
		if err := forceHTTP11ALPN(uConn); err != nil {
			_ = plainConn.Close()
			return nil, err
		}
		err = uConn.HandshakeContext(ctx)
		if err != nil {
			_ = plainConn.Close()
			return nil, err
		}
		if negotiated := uConn.ConnectionState().NegotiatedProtocol; negotiated != "" && negotiated != "http/1.1" {
			_ = uConn.Close()
			return nil, fmt.Errorf("unexpected ALPN protocol negotiated: %s", negotiated)
		}
		return uConn, nil
	}
}

func forceHTTP11ALPN(uConn *utls.UConn) error {
	if err := uConn.BuildHandshakeState(); err != nil {
		return err
	}
	for _, ext := range uConn.Extensions {
		alpnExt, ok := ext.(*utls.ALPNExtension)
		if !ok {
			continue
		}
		alpnExt.AlpnProtocols = []string{"http/1.1"}
		return nil
	}
	return nil
}
