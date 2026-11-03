# Web arayüzünde GPU seçimi

Güncelleme: 1 Kasım 2026. [Güncel Word kullanım kılavuzu](KULLANIM_KILAVUZU_TR_GUNCEL_20261101.docx) ve [proje durum raporu](RELEASE_READINESS.md). Güncel menü yolları ve bağlantı sınırlamaları bu kılavuzdadır.

1. Proje klasöründe `./start_gui.sh` çalıştırın. Arayüz, normal Chrome profilinizden ayrı bir uygulama profilinde Google Chrome ile açılır. macOS’ta Chrome bulunamazsa terminal kurulum izni ister; `y` ile onaylarsanız Google’ın resmi kurulum dosyası indirilir. AI modelleri indirilmez. Windows ve Linux’ta Chrome yoksa önce resmi Chrome kurulumunu tamamlayın.
2. **Offline dubbing · GPU provider** alanında **Local**, **Colab CLI — G4 GPU** veya **Modal — full video dubbing** seçin.
3. Bilgisayarınızdaki videoyu seçin. Kaynak dili ve hedef dili belirleyin. Örneğin **English → Turkish**.
4. **Start Dubbing** düğmesine basın. Bulut bağlantısı hazır değilse uygulama önce bağlantıyı ve kurulumu yapar; hazır olunca aynı işi başlatır. İsterseniz önceden **Connect & prepare GPU** kullanabilirsiniz.
5. Hesap girişi gerekiyorsa GUI’de görünen yetkilendirme bağlantısını açın. Google bir kod gösterirse GUI’deki kod alanına yapıştırın; yalnız izin onayı isteniyorsa onaydan sonra **Continue after authorization** düğmesine basın. Şifreyi uygulamaya yazmayın.
6. Düğmeye bastıktan sonra üstteki işlem bildirimini izleyin. İstek gönderilmesi, işin tamamlanması anlamına gelmez. Hazırlık ve iş durumu sistemden geldikçe güncellenir; hata mesajı sayfada görünür kalır. Daha fazla ayrıntı için bağlantı günlüğünü ve **Process Log** alanını kullanın. Sonuç videoyu arayüzden oynatın veya indirin.
7. Model seçimi, cihaz, konuşmacı sayısı, terim sözlüğü ve lip-sync için **Advanced model, speaker and lip-sync settings** alanını açın. Local model indirme/kurulum düğmeleri burada bulunur. Live bölümündeki dil/yakalama/Finish kontrolleri ise açılan uzantı panelindedir.

Arayüz %100 yakınlaştırmada kompakt masaüstü ölçülerine sahiptir; dar ekranda bölümler alt alta gelir. Kod güncellendiyse yeni görünüm Python GUI sunucusu yeniden başlatıldığında yüklenir. Yalnız sayfayı yenilemek, eski sunucu sürecindeki arayüz kodunu değiştirmez.

## Live Dubbing

1. Aynı GUI’nin **Live Dubbing · GPU provider** alanından **Local** veya **Colab CLI** seçin. Offline bölümündeki seçim bundan bağımsızdır.
2. Colab’da isterseniz **Connect & prepare Colab** düğmesiyle oturumu önceden hazırlayın. **Open Live Dubbing Browser** düğmesine doğrudan basarsanız bağlantı ve tarayıcı açılışı tek akışta yapılır.
3. Açılan Chrome penceresinde yetkili canlı yayını veya desteklenen videoyu açın ve uzantıyı bu kaynak sekmesinde açın. Ana GUI’de **Create one-use pairing code** ve ardından **Copy pairing code** düğmesine basın; kodu uzantı panelindeki **Pairing code from the local application** alanına yapıştırın. Kod 120 saniye içinde kullanılmalıdır.
4. Kaynağı yakalama ve çevirme yetkiniz olduğunu onaylayıp **Start Live Dubbing** düğmesine basın. Modeller hazırlandıktan sonra kaynak sekmesinde **Choose permitted source & capture** düğmesine basarak sesi başlatın.
5. Bitirirken **Finish capture & play remaining speech** ile sıradaki doğrulanmış konuşmaların bitmesini bekleyin; sonra **Stop** ile yakalamayı durdurun. Colab GPU’su açık kalır. İşiniz bittiğinde ana GUI’den **Disconnect GPU** seçin.

Live için Local ve Colab CLI kullanılabilir. **Modal Live Dubbing henüz uygulanmadığı için** listede kullanılamaz; Modal mevcut GUI’de Offline Dubbing içindir. Terminal kısayolu `./start_live_dubbing_browser.sh` de Local ve Colab seçimlerini kullanmaya devam eder.

## Colab CLI

Oturum adı, uzak klasör yolu, notebook veya Colab terminal komutu gerekmez. Uygulama yalnız kendisine ayrılmış `video-dubbing-g4` oturumunu oluşturur veya yeniden kullanır. İlk hazırlıkta gerekli Python ortamı ve eksik modeller G4’ye kurulur; yerel AI ortamınız değiştirilmez.

Bağlantıdan önce uygulama `colab usage` ile Compute Unit bakiyesini kontrol edip ekranda gösterir. 1 CU altı düşük bakiye olarak belirtilir; sıfır bakiye veya sorgu başarısızlığı ayrı açıklanır. Bu tutar G4 tahsisini garanti etmez: Colab erişim ve kaynak sınırları ayrıca değişebilir. Bağlantı hatasında Colab hesabınızdaki bakiye ve kaynak durumunu kontrol edin.

Modeller Colab sanal makinesinde saklanır ve aynı oturum açık kaldıkça yeniden kullanılır. Sanal makine sonlandığında modeller kaybolur; yeni oturumda tekrar indirilmeleri gerekir. Colab CLI için normal hesap girişi istenebilir.

Video önce Colab’a aktarılır, tam dublaj hattı G4’de çalışır ve sonuç bilgisayara indirilir. İş tamamlandığında veya iptal edildiğinde G4 açık kalır. Yalnız **Disconnect GPU** kapatma ister. Bu düğmeye basana kadar ayrılmış kaynaklar kredi tüketebilir. Aynı oturumda başka notebook işleri çalıştırmayın.

## Modal

Yeni **Modal** seçeneği tam çevrimdışı dublajı uzaktaki GPU’da çalıştırır. İlk bağlantıda geçici uygulamanın ortamı ve kalıcı model Volume’u hazırlanır. Her işin videosu otomatik yüklenir ve sonuç geri indirilir. Modal uygulaması ve GPU, işten sonra bağlantıda kalır. Yalnız **Disconnect GPU** uygulamanın ve GPU konteynerlerinin kapatılmasını ister; model Volume’u sonraki iş için saklanır. Volume depolama ücreti kalabilir. Eski yalnız VoxCPM2 seçeneği ileri kullanım için korunur.

## İzinler ve kapanış

Community-1 için Hugging Face koşullarının sizin hesabınızda kabul edilmiş olması gerekir. İsteğe bağlı konuşmacı modeli bölümüne bir okuma anahtarı girebilirsiniz; anahtar yalnız kurulumda kullanılır ve proje ayarlarına kaydedilmez. Model yoksa mevcut bölüm bazlı ses referansları kullanılır.

GUI sunucusu yeniden başlatıldığında önceki Colab bağlantısının bellekteki durumu otomatik devralınmaz. Bu nedenle panel bağlantısız ve **Disconnect GPU** pasif görünürken eski G4 oturumu açık olabilir. Yeni kaynak ayırmadan önce `colab sessions` ile kontrol edin; işi biten yalnız bu uygulamaya ayrılmış oturumu gerektiğinde `colab stop --session video-dubbing-g4` ile açıkça kapatın. Bu komut oturumun tamamını sonlandırır. Bu sınırlama UI yenilemesinde giderilmedi; ayrıntı [sağlayıcı belgesinde](COMPUTE_PROVIDERS.md#gui-restart-and-connection-state).

İşin tamamlanması, hata vermesi, GUI sekmesinin veya GUI sunucusunun kapanması bulut oturumunu kapatmaz. Kapatmak için GUI’de **Disconnect GPU** düğmesine basın. Sağlayıcının kendi zaman/kota sınırları ayrıca geçerlidir. Sonuç video yerel dosyadır; GPU kapandıktan sonra da saklanır. Yeni GUI açılışında önceki sonuç panelden temizlenir; dosyaya **Open Output Folder** ile ulaşabilirsiniz.

Colab CLI’nin macOS/Linux sınırı devam eder; Windows’ta bu seçenek devre dışıdır. Modal canlı dublaj için sunulmaz. Daha büyük Hy-MT2 modelleri ve farklı platformlar için doğrulama kapsamını [sürüm kayıtlarından](release/GUI_COMPUTE_E2E_20261101.md) kontrol edin.
