# Çok dilli canlı dublaj: araştırma ve uygulama kararı

26 Ekim 2026. Durum: araştırma ve aday doğrulaması sürüyor. Bu belge bir üretim yeterlilik beyanı değildir.

## Sorun ve değişen yön

Kullanıcı İngilizce/Türkçe gramer istisnalarıyla ilerlenmesini reddetti. `syntax.py`, İngilizce bağlaç listeleri ve benzeri kurallar genel canlı dublaj çözümü sayılmayacak. Onaylı offline video/pipeline korunacak; VoxCPM2 değiştirilmeyecek. GPU/model dosyaları yalnız Colab G4 üzerinde bulunacak. Yayın, commit veya push yapılmayacak.

Gerçek testler aynı kaynak dilinde bile yeni gramer kurallarının başka doğal ifadeleri engellediğini gösterdi. Son canlı test iki gerçek oynatmadan sonra durdu. Sorun yalnız hız değil: ASR hatası çeviriye aktarılıyor, bütünce kararlılığı ile anlam tamamlanması karıştırılıyor ve belirsiz bir son, önceki tamamlanmış içeriği kilitleyebiliyor.

## Resmi kaynakların gösterdiği seçenekler

| Aday | Bulgular | Bu proje için karar |
|---|---|---|
| [Google gerçek zamanlı S2ST](https://research.google/blog/real-time-speech-to-speech-translation/) | Streaming audio modeli ve zaman eşlemeli eğitim; klasik kaskadın gecikme/hata birikimini ele alıyor. | Ürün hedefi için yöntemsel referans. Kapalı modelin ölçümleri bizim sisteme aktarılmayacak; harici API zorunluluğu eklenmeyecek. |
| [SimulStreaming](https://github.com/ufal/SimulStreaming) ve [IWSLT makalesi](https://aclanthology.org/2025.iwslt-1.41/) | Whisper üzerinde attention tabanlı akış; ASR ve MT durumlarını ayırıyor. | Kaynak kararlılığı için güçlü aday. Hazır EuroLLM hedef kapsamı bizim 30 dilimizin tamamını karşılamıyor. |
| [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit) | Yerel akış, oturum yönetimi ve farklı ASR/MT adaptörleri sağlıyor. | Tüm uygulamayı değiştirmek yerine uygun akış bileşenleri değerlendirilecek. Mevcut güvenli capture ve A/V oynatıcı korunacak. |
| [AlignAtt4LLM](https://github.com/QuentinFuxa/Alignatt4LLM) | Öğrenilmiş attention başlıklarıyla kaynak destekli target-prefix üretimi; yayımlanan deneyler sınırlı dil yönlerinde. | Dil yönü kalibrasyonunu bütün dillere kanıtsız genellemek uygun değil. Protokol/policy referansı; otomatik evrensel çözüm değil. |
| [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) | Yerleşik streaming seçeneği var; resmi 30 dil listesi VoxCPM2'nin 30 diliyle aynı değil. | Tek başına evrensel ASR değişimi olarak seçilmedi. Dil sayısı eşitliğinin kapsam eşitliği olmadığı kaydedildi. |
| [Omnilingual ASR](https://github.com/facebookresearch/omnilingual-asr) | Çok geniş dil kapsamı; resmi genel inference arayüzü doğrudan gerçek zamanlı streaming sağlamıyor. | Kapsam güçlü; akış ve timestamp maliyeti ölçülmeden mevcut çalışan ASR yerine geçirilmeyecek. |
| [SaT](https://github.com/segment-any-text/wtpsplit) | Öğrenilmiş çok dilli cümle sınırları; model listesinde 85 dil var. | Resmi `sat-3l-sm` eğitim/dil listesinde Lao, Swahili ve Tagalog yok. Tek zorunlu güvenlik kapısı yapılamaz; ortak ek sınır sinyali olarak genel-geçer modelin bu dillerdeki davranışı da ölçülecek. Noktalama tahmini anlam garantisi değildir. |
| [Smart Turn](https://github.com/pipecat-ai/smart-turn) | Ses üzerinden konuşma sonu modeli; 23 dil için duyurulmuş destek. | Bütün 30 dil için doğrulanmış ortak kapı sayılmayacak. |
| [SeamlessStreaming](https://github.com/facebookresearch/seamless_communication/blob/main/docs/streaming/README.md) | Doğrudan çok dilli streaming S2TT, 96 text hedefi. | Teknik açıdan ilgili. [Resmi lisans ayrımı](https://github.com/facebookresearch/seamless_communication#license) Streaming ağırlıklarını CC-BY-NC-4.0 olarak tanımlıyor; ayrı Seamless lisansı birleşik/Expressive modeller için. Streaming ticari kullanımı kapsamaz; ayrıca eski fairseq2/CUDA yığını RTX Blackwell için doğrudan doğrulanmış değil; genel üretim backend'i olarak seçilmedi. |
| [TranslateGemma](https://huggingface.co/google/translategemma-12b-it) | Çeviriye özel model; erişim manuel gated ve Gemma lisanslı. | Lisans/erişim ve tam dil matrisi çözülmeden otomatik indirme/varsayılan değişimi yok. |
| [MADLAD-400](https://huggingface.co/google/madlad400-10b-mt) | Geniş kapsamlı Apache-2.0 MT; 30 hedef için dil kodları mevcut. | 3B ve 10B yalnız Colab’da kısa gerçek MT karşılaştırmasında sınandı; aşağıdaki sonuçlar nedeniyle varsayılan yapılmadı. |
| [Gemma 4 E4B](https://huggingface.co/google/gemma-4-E4B-it) | Apache-2.0, açık erişim, ses + metin girişi, çok dilli text çıkışı. | Tek bir ortak anlamsal birim/çeviri arayüzü için ilk sınırlı aday. Model kartı bütün 30 dilde kararlılık kanıtı sayılmayacak. |

## Seçilen ortak tasarım ilkeleri

1. Kaynak ASR kararlılığı, çeviri taslağı ve gerçekten konuşulmuş target prefix ayrı tutulur. Tekrarlanan ASR kelimesi tek başına anlam tamamlandı demek değildir.
2. Dil adı/kodu ve model kapsam matrisi dışında dile özgü gramer, bağlaç, fiil veya kelime listesi karar mekanizması olmayacak.
3. Kaynak birimler zaman damgalı ve indeksli olur. Çince/Japonca/Thai gibi metinlerde boşluk sayısı sözcük/ifade sınırı kabul edilmez. Çıkış kararlılığı Unicode karakter/grapheme düzeyinde incelenir; oynatmaya yalnız doğrulanmış tam target birimi verilir.
4. Aynı çok dilli model arayüzü güncel kaynak, güvenilir geçmiş bağlam ve isteğe bağlı ses kanıtıyla `wait` veya sınırlandırılmış bir kaynak-prefix önerisi + çeviri döndürür. Çıkış strict şema ve kaynak sınırlarıyla doğrulanır. Modelin uydurma güven puanı kullanılmaz.
5. Öneriler gerçek ardışık gözlemlerde kararlı olmadan konuşulmaz. Oynatılmış kaynak/target konumları değiştirilemez. Sonradan gelen revizyon eski sesi tekrar başlatamaz.
6. Sessizlik tek başına dilbilgisel tamlık değildir. Aynı politika bütün dil yönlerinde uygulanır; modelin öğrenilmiş bağlam kararı ile gerçek akustik/timing sinyalleri birlikte kullanılır.
7. Kuyruklar ve revizyon süresi sınırlı kalır. Çözülemeyen kaynak aralığı açıkça raporlanır; başka tamamlanmış içerik sessizce yok edilmez. Backlog ile anlamsal belirsizlik ayrı hata nedenleridir.
8. VoxCPM2'nin native PCM üretimi, immutable speaker conditioning cache, bağımsız TTS QA, bir bounded regeneration ve gecikmeli A/V timeline korunur. TTS QA'nın ASR'sine beklenen target transcript veya sözlük ipucu verilmez.
9. Başka servis zorunlu olmaz. Colab yalnız geliştirme/test altyapısıdır. Kurulum kullanıcı açıkça indirmedikçe büyük model indirmez.

## İlk aday deneyi ve uygulama sırası

Önce Gemma 4 E4B tek sıcak Colab modeliyle sınanacak: gerçek hatalı kaynaklar, tam/yarım anlamsal birimler, farklı yazı sistemleri, sayılar ve teknik anlam. Greedy/deterministik deney, EOS/token/duvar zamanı ve ham cevaplar saklanacak. Metin deneyiyle ses koşullu deney ayrı raporlanacak. Başarılı runtime çalışması anlamsal başarı sayılmayacak.

Ardından genel source/target commitment protokolü, indeks/Unicode ve bounded recovery testleri uygulanacak. İngilizce parser ve gramer istisnaları normal Live yolundan çıkarılacak. Yeni politika offline akışa taşınmayacak. ASR frontend değişimi ancak aynı gerçek kayıtlarla ölçülmüş iyileşme sağlarsa yapılacak.

Son doğrulama: çeşitli dillerde gerçek zamanlı dosya besleme; gerçek YouTube VOD ve canlı; uzak speaker dönüşleri; bilinen referanslarla ad/terim/sayı ve negasyon koruması; gerçek ses/RTF/TTFA ve queue/drift. Bütün desteklenen diller için aynı test protokolü kullanılacak. Test edilmemiş dil yönlerine “kararlı çalışıyor” etiketi verilmeyecek. Belirli birkaç dildeki yayın/benchmark sonucu tüm dil çiftleri için kanıt sayılmayacak.

## Mevcut durum

Araştırma ve ilk aday seçimi yapıldı. Ortak politika artık LiveSession'a bağlandı ve gerçek altı dil testi yapıldı; üretim kabulü henüz geçmedi. Önceki gramer temelli controller ve syntax modülü tarihsel regresyon incelemesi için duruyor; normal LiveSession onları çağırmıyor, `LIVE_SYNTAX_MODEL` değişkeniyle de etkinleşmiyor. Hy-MT2 30B indirilmedi; önceki 7B karşılaştırması tek başına bir model terfisine yeterli bulunmadı.


## İlk gerçek aday sonucu: prompt tabanlı anlamsal karar reddedildi

Gemma 4 E4B, Colab üzerinde 14 gerçek-model isteğinde çalıştı; indirme 40.981 s, model yükleme 1.883 s, toplam 59.606 s. Hepsi EOS ile bitti. Dört fixture'da beklenen kaynak/bekle kararı karşılanmadı: gerçek canlı kaynakta kararsız sınırın ötesini tüketme, relative prefix'e erken izin verme ve Almanca/Türkçe kesik koşulları tam birim sanma. İlk hata strict kaynak-frontier doğrulamasınca yakalandı. Böyle bir model-istemini tek başına canlı read/write politikası yapmak reddedildi. Bazı çevirilerin iyileşmesi bu sınır başarısızlıklarını geçersiz kılmaz.

Bu sonuç, [stable-prefix eğitimi çalışmasının](https://arxiv.org/abs/2609.05799) genel bir offline LLM ile streaming kararını ayırmasının neden önemli olduğunu gösteriyor. [Pinch-AST](https://aclanthology.org/2026.iwslt-1.30/) de karakter düzeyinde re-translation/LCP yaklaşımını kullanıyor; o deneyin per-pair LoRA sonuçları bizim yapılandırmamıza aktarılmayacak.

`live_dubbing/commitment.py` ve `tests/test_multilingual_commitment.py` eklendi. Aynı şema/gate boşluklu, CJK, RTL ve birleştirici karakterli metinlere uygulanıyor; sekiz test geçti. Kararsız kaynak tüketimi, tekrar eden aynı gözlemin iki kanıt sayılması, kaynak replay'i, timestamp grubu bölünmesi ve geç gelen revizyonla eski adayın kabulü engelleniyor. Bu altyapı daha sonra normal LiveSession yoluna bağlandı; test başarısı model anlamsal başarısı değildir.

Sonraki ölçüm aynı Gemma modelinin ses koşullu ASR/AST'si: altı FLEURS dili ve iki gerçek sorunlu YouTube penceresi. Altı FLEURS örneği için kaynak transcript ve aynı ID'li paralel hedef referansları edinildi; referanslar modele verilmeyecek. Audio inference, text-only sınır sınıflaması ve gerçek streaming kabulü ayrı raporlanacak.

## Ortak politikanın gerçek ilk uygulaması

`universal.py`, `segmentation.py` ve özel JSON-lines CPU worker eklendi. SaT 3l-sm tek model/tek eşik kullanıyor; dile göre LoRA veya gramer listesi yok. Girişin son karakterindeki model puanı kesinlikle sınır kanıtı sayılmıyor. Devam eden konuşmada gerçek sağ bağlam ve akustik lookahead, konuşma sonunda tüm kaynak biriminin ASR'de tekrarlanması ve gerçek sessizlik gerekiyor. Akustik turn birimi, dilbilgisel tamlık garantisi diye sunulmuyor. Çeviri iki gerçek ASR gözleminde kararlı olmadan TTS'ye verilmez; oynatılan konum değiştirilemez.

ASR'nin özgün surface boşlukları korunuyor; CJK metnine yapay sözcük boşlukları eklenmiyor. Unicode birleştirici işaretler ASR agreement karşılaştırmasından silinmiyor. Model capability router'daki gerçek hata düzeltildi: yedi fallback dil yalnız hedefte değil kaynakta da kontrol ediliyor. Offline pipeline ve VoxCPM2'nin üretimi değiştirilmedi. Kurulum açık bir `setup_live_segmentation.py` komutuyla yapılır; status/app launch indirme yapmaz.

Önceki VM'de 153 unit test geçti. Yeni G4 oturumunda altı FLEURS kaydı 20 ms paketlerle gerçek zamanlı, tek sıcak native GPU model setine beslendi. Tam referanslar modele verilmedi; test sonuna eklenen 1.5 s sessizlik manifestte açıkça kayıtlıdır. Sonuçlar `temp/live-validation-20261026/universal-natural-evidence-1792997597.zip` içinde (68,686 B, SHA-256 `2e4133045e6806e942ba3fa4fe57567643eefcd673c007e3c75c6b6804d52249`). Bu bir tarayıcı oynatma kabulü değildir; gerçek oynatma ACK'i üretilmedi.

| Kaynak → hedef | Runtime | Kaynak CER tanı değeri | Gerçek native sentez |
|---|---|---:|---:|
| EN → TR | Tamamlandı | 0.01235 | 1 birim |
| TR → EN | Tamamlandı | 0.01389 | 1 birim, 1 QA retry |
| ZH → EN | Tamamlandı | 0.00000 | 1 birim |
| AR → EN | Tamamlandı | 0.04706 | 1 birim |
| DE → EN | Tamamlandı | 0.00000 | 2 birim |
| LO → EN | Başarısız | Geçerli çıktı yok | Giriş kuyruğu taşması; 0 birim |

Runtime başarısı çeviri doğruluğu değildir. Elle incelenen gerçek çıktılarda önemli hatalar bulundu: Almancadaki T-Rex cümlesinin güç ilişkisi ters çevrildi; Arapça "onuncu fırtına / subtropical" ifadesi "ten storms / tropical" oldu (ASR'de de ordinal bozulmuştu); Türkçe sezyum terimi hedef İngilizcede düzeltilmeden kaldı. İki gözlemde aynı çeviri çıkması bu yanlışları doğrulamaz. Dolayısıyla bu sürüm tüm diller için kalite kabulü almadı.

Lao için ek gerçek ASR teşhisi `multilingual-asr-cadence-diagnosis.json` olarak saklandı. 7.56 s ses Whisper'da 224 segment tokenıyla 4.625 s sürdü ve Khmer benzeri tekrarlar üretti. Süreye bağlı 214 token limitiyle de yanlış dil/tekrar devam etti. Bu nedenle yalnız token kırpma çözüm olarak alınmadı ve böyle bir ASR yaması eklenmedi. Ortak alternatif olarak [Omnilingual ASR CTC 1B v2](https://github.com/facebookresearch/omnilingual-asr) aynı altı doğal kaynakla karşılaştırıldı (sonuçlar aşağıda). Resmi model geniş kapsamlı ve Apache-2.0; resmi inference API'si streaming sayılmıyor. Kayar pencere ve gerçek CTC timestamp/kararlılık davranışı ayrıca doğrulanmadan normal ASR yerine geçirilmeyecek. Python 3.11 / Torch 2.8 / fairseq2 0.6 ortamı yalnız Colab'da izole kuruldu; ana Vox ortamı yükseltilmedi.

Gemma audio adayı da varsayılan yapılmadı: ilk sekiz çağrı strict JSON sözleşmesini bozdu; Lao örneğinin dili/anlamı ve teknik terimler de yanlıştı. Sonlanan eski VM'deki henüz indirilmemiş ham Gemma JSON/logları kaybedildi; bu bölüme ait sayılar önceki terminal gözlemleridir, yeni bir ham artifact gibi sunulmamalıdır. Modeli yalnız kayıtları yeniden üretmek için tekrar indirmiyoruz.


## Gerçek model karşılaştırmaları ve karar

Aynı altı FLEURS kaydının tam sesiyle dört Omnilingual 1B varyantı Colab CUDA üzerinde çalıştırıldı. CTC v2 ve LLM v2 Lao kaydında sırasıyla 0.95556 ve 0.22222 CER tanı değeri verdi. LLM v2 tokenizer round-trip testi, doğru Lao kaynak metninde dokuz bilinmeyen token buldu; bu, model boyutunu aynı vocabulary ile artırarak çözülemez. CTC v1 / LLM v1 Lao sonuçları 0.83333 / 0.16667 idi; v1 çıktıları 1963 yılını atladı. İngilizce v1 çıktılarında da 25/30 sayıları kayboldu. Düşük CER, kritik sayı kaybını kabul edilebilir yapmaz. Hiçbiri genel ASR olarak terfi ettirilmedi.

Omni ortamı Python 3.11.17, Torch/torchaudio 2.8.0 CUDA 12.8, fairseq2 0.6, omnilingual-asr 0.2.0 ve PyArrow 20 ile ana Vox ortamından izole tutuldu. İlk PyArrow 26 / NumPy 1.26 ABI hatası yalnız bu ortamda düzeltildi. CTC modellerinin sıcak tam-ses inference süresi yaklaşık 0.020 s, LLM varyantlarında yaklaşık 0.147–0.780 s idi; bunlar gerçek streaming veya uçtan uca gecikme ölçümü değildir. Ham küçük JSON kayıtları `temp/live-validation-20261026/omniasr-*.json` içindedir.

MADLAD 3B ve 10B, her biri tek sıcak modelle on gerçek metin çevirisinde sınandı; bu deney ASR veya TTS testi değildir. Altı doğal kaynak için ground-truth kaynak transcript kullanıldı. Almanca güç ilişkisinin ters çevrilmesini ve Arapça ordinal/subtropical örneğini iyileştirdiler. Buna karşılık Lao’da 3B sediment taşıma bilgisini atladı; 10B sediment kavramını destruction olarak çevirdi. 10B teknik ifadelerde de tam bir iyileşme sağlamadı. Dolayısıyla ikisi de genel varsayılan yapılmadı. Sıcak generation süreleri 3B için 0.255–0.772 s, 10B için 0.292–0.841 s; tüm çağrılar EOS ile bitti. Otuz dil etiketi bulunması yalnız tokenizer kapsamıdır, gerçek otuz dil kalite benchmark’ı değildir.

## Ses alımı ile inference arasındaki gerçek darboğazın düzeltmesi

Lao teşhisindeki 4.625 s ASR çağrısı sırasında `_ingest` de bekliyordu; bu yüzden iki saniyelik capture kuyruğu doluyordu. LiveSession artık PCM/VAD alımını ayrı tutar. Tek decoder task, işlem sürerken gelen istekleri en güncel ses zamanına birleştirir; eski ASR işleri kuyruğu oluşturmaz. Model çağrısı değişmeyen bir ses snapshot’ı ve yalnız o snapshot’a ait akustik kanıt kullanır. Yeni gelen konuşma, eski bir commit sonrasında kaybolmaz. Çözülemeyen kaynak PCM sınırı ve açık hata politikası korunur; ses düşürülmez.

Colab’da 157/157 unit test geçti (6.616 s). Yeni test, recognizer beklerken dört saniyelik PCM’nin tamamının VAD’ye ulaşmasını ve yalnız tek aktif ASR çağrısı olmasını denetler. Bu bir unit scheduling testidir; gerçek Lao anlam/dil sorununu çözmüş sayılmaz. Sonraki gerçek YouTube ölçümleri ayrı kaydedilir.


## 7B ASR, gerçek uzun bağlam ve tarayıcı devamı

Resmi 7B dil tablosunda Lao CER 6.6 olarak raporlanıyor; bu dış sonuç bizim ölçümümüz değildir ([Meta CSV](https://github.com/facebookresearch/omnilingual-asr/blob/main/per_language_results_table_7B_llm_asr.csv)). V1 variant7 tokenizer Lao karakterlerini temsil edebildiği için yalnız bir ek 7B karşılaştırması yapıldı: altı gerçek doğal ses, kaynak dili metadata olarak, beklenen transcript verilmeden. Toplam 145.722 s; indirme/yükleme 142.597 s, inference 0.179–0.861 s. Lao CER 0.100 ve 1963 korunmuş olsa da İngilizcede 25 → 2-5, Almancada T. Rex → tieres hataları kaldı. Bu aday da üretim varsayılanı yapılmadı. 7B ağırlıkları yalnız Colab’da bulundu; bu Omni ASR modelidir, Hy-MT2 30B değildir.

Dört hedefte (TR/DE/ZH/AR) toplam 12 gerçek Hy-MT2 çevirisi, aynı Live draft fonksiyonu ve SQLite belleğiyle sınandı. Her hedefin iki uzak sorgusundan önce 120 açıkça sentetik storage distractor eklendi ve SQLite kapatılıp açıldı. Bu 120 satırın tamamı çevrilmiş bir diyalog gibi sunulmaz. Sekiz uzak sorgunun tamamında ilk kaynak bağlam geri geldi. RAG ve 64/25 sayıları bütün çıktılarda bulundu. Açık eşlemelerin literal korunması ise her çıktıda sağlanmadı; lokalize ad/birim tek başına anlam hatası sayılmaz, fakat verilmiş birebir sözlüğe uyulduğu da iddia edilemez. Bu test ASR/TTS/tarayıcı testi değildir.

Gerçek M2M100 sorgusu ikinci bir kod kusurunu gösterdi: kaynak `The memory is ready.`, ham çıktı `Muisto on valmis.`, eski backend çıktısı `Muisto on valmis. Muistijärjestelmä` idi. Eksik sözlük terimi sonradan bağımsız sözcük olarak ekleniyordu. Bu davranış kaldırıldı. NFC/casefold tabanlı ortak literal eşleme denetimi Live taslaklarına ve fallback backend’ine eklendi. Aynı gerçek M2M isteği düzeltmeden sonra `explicit_glossary_not_preserved: memory` ile çıktı döndürmeden reddedildi. Bu bir anlam doğruluğu modeli değildir; yalnız kullanıcının açık terim eşlemesini koruma sözleşmesidir. 160/160 unit test 6.745 s’de geçti.

Async ingest düzeltmesinden sonraki Lao tekrarında 453/453 PCM paketi alındı, kuyruk taşmadı (7.56 s ses + 1.5 s açık endpoint sessizliği). Fakat MT fallback cache çözümlemesi başarısız oldu. Ağırlıklar ve vocab gerçekten mevcuttu; eksik `refs/main` metadata’sı tokenizer’ın dosya yolunu `None` yaptı. Resmi Hugging Face `hf_hub_download` ile mevcut `vocab.json` çözümlemesi reference’ı `55c2e61bbf05dfb8d7abccdc3fae6fc8512fd636` olarak kurdu; ağırlık indirilmedi. Sonraki gerçek M2M istekleri cache’den yüklendi. Lao'nun kaynak ASR anlam/dil kusuru böylece çözülmüş sayılmıyor.

İki yeni gerçek Chrome denemesi, ortak politika ve async ingest ile yapıldı: canlı TalkTV `5587f61bf9e74a10aef150c92bbf6d71`, normal Stanford videosu `5eaea5d3e7ed4e288051df3f1b0c8dab`. İkisi de 16 s kaynak belirsizlik bütçesinde durdu; input queue ölçümü 0 s idi. Canlıda tek MT taslağı oluştu; ikisinde de TTS/played sayısı 0. Dolayısıyla yeni politika altında YouTube kabulü geçmedi. Hata anında capture ve GPU model süreçleri bırakıldı. Normal video için kullanıcı YouTube girişini kendi tamamladı; çerez/profil kopyalanmadı.

SaT 12l-sm ayrıca aynı kayıtlı doğal ASR pencereleri üzerinde CPU’da karşılaştırıldı (revision `d70c72a9331b2d5a9e82baad00c64964a23a09bb`, 556,287,754 B ONNX, yalnız Colab). Bazı puanlar değişti; güvenilir bir erken sınır iyileşmesi kanıtlanmadı. Varsayılan 3l-sm değiştirilmedi. Whitespace/karakter sınırı eşlemesi de kayıtlarda incelendi; bu iki başarısızlığı açıklayan bir separator-index kusuru bulunmadı. Yalnız varsayım üzerine kod yaması yapılmadı. Quality modunda `bf510694c834438e828db65fd36a2918` bir gerçek native ifadeyi oynattı (TTFA 0.385338 s, RTF 0.769799, speech-end→playback 7.362800 s, A/V −0.089 s), sonra ikinci uzun birimde hedef taslak uzlaşması sağlanmadığı için durdu. N=1 ölçüm uzun süreli drift veya semantik kabul değildir. Önceki Balanced başarısızlıkları korunur. Güncel ayrıntılı rapor: `temp/live-validation-20261026/CURRENT_VALIDATION_REPORT_TR.md`.


## Devam eden tek güçlü metin adayı

[Qwen3.5-27B resmî kartı](https://huggingface.co/Qwen/Qwen3.5-27B) Apache-2.0, açık erişim ve geniş çok dilli metin kapsamı sunuyor. Resmî WMT24++ skoru XCOMET-XXL tabanlı olduğundan insan semantik kabulünün yerine kullanılmıyor. [Transformers arayüzü](https://huggingface.co/docs/transformers/model_doc/qwen3_5) mevcut Colab Torch/Transformers sürümünde destekleniyor; ana Vox ortamına yeni paket veya sürüm yükseltmesi yapılmadı.

Bu aday, kalan bağlam/çeviri/kararlılık sorunu için yalnız bir bounded metin karşılaştırmasına alındı. Revision `fc05daec18b0a78c049392ed2e771dde82bdf654`, toplam 55,563,022,432 B ağırlık yalnız Colab diskine indiriliyor; başlangıçta 69.3 GB boşluk ve 10 GiB ilave disk rezervi kontrol edildi. Download process'i 600 s ile sınırlı. Sonraki 600 s inference sınırı içinde tek sıcak modelde 25 kısa istek hazırlanıyor: 10 MT, 8 yarım/tam ifade, 5 kontrast doğrulama ve 2 gerçek uzun birim kontrolü. Beklenen karar/hedefler model payload'ına verilmez. Non-thinking chat template ve EOS/token/ham JSON/duvar zamanı kaydı kullanılacak. Bu kayıt henüz başarı, yeni bir üretim backend'i veya varsayılan değişikliği değildir.


## Qwen gerçek sonuçları ve kaynak taahhüdü v2

Qwen3.5-27B tek sıcak modelde 25 gerçek metin çağrısını 44.208 s’de tamamladı; model yükleme 5.102 s, tepe tensor allocation 55,069,554,176 B. Bütün yanıtlar geçerli JSON ve EOS içerdi. On MT örneğinde önceki Almanca ters ilişki, Arapça ordinal/subtropical ve Lao sediment sorunları iyileşti; mecaz/scope kusurları kaldı. Sekiz yarım/tam kaynak kontrolünde 7/8, altı anlam doğrulama kontrolünde 5/6 doğru karar verdi. Bu yüzden model **yalnız isteğe bağlı çeviri backend’i** olarak eklendi; endpoint veya semantik güvenlik hakemi yapılmadı. Varsayılan Hy-MT2 ve offline yol değişmedi. Modelin resmî dil sayısı bütün dillerde kalite garantisi sayılmıyor.

Native Live entegrasyonunda gerçek 10.56 s İngilizce FLEURS sesi 20 ms paketlerle gerçek zamanda beslendi: tek native ifade, 38 PCM chunk, 0 hata/çözülemeyen tail; oynatıcı olmadığı için played ACK üretilmedi. 25/30 korunmuş Türkçe çıktı, TTFA 0.181183 s, RTF 0.374210. Bu tek örnekte speaker similarity null olduğundan klonlama başarısı iddia edilmez.

Gerçek YouTube VOD denemesi `f220651460fc486ab4111f0412664c6f`, aynı çeviriyi birebir bekleyen v1 kuralının yazım/diakritik/dolgu sözcüğü değişikliklerinde durduğunu gösterdi. Bu eşitlik anlam doğruluğunu da kanıtlamıyordu. `confirmed_source_contextual_mt_v2` artık iki gerçek ASR gözleminde doğrulanan kaynak ve ayrı held boundary sonrasında tek tamamlanmış bağlamsal çeviri seçer. Ses QA’sı ve değiştirilemez kaynak/oynatım sahipliği korunur; semantik doğruluk sertifikası yazılmaz.

İlk v2 VOD `3da8a9a054e64438a62d04f687e650cd` iki native Türkçe ifadeyi gerçekten oynattı; A/V −0.036/−0.075 s, speech-end→playback 10.8541/11.6081 s. Ardından uzun üçüncü kaynakta 18 s Quality bütçesi tükendi. Gerçek Silero tekrarında bu kaynakta 650 ms sessizlik bulunmadı; SaT’nin geçici tam sınırı sonraki pencerede geri çekildi. Bu sonuç korunmuştur. Quality/Balanced/Low Latency kaynak bütçeleri 24/20/16 s olarak, aynı ASR/sınır koşulları korunarak sınırlı bağlam denemesine alındı. Bu değişiklik zorunlu metin kırpma veya daha düşük sınır eşiği içermez. Yeni sonuç ayrı kaydedilecektir.

[SimulStreaming’in resmî uygulaması](https://github.com/ufal/SimulStreaming) streaming ASR ve çeviride ayrı kararlılık politikalarını kullanır; [WhisperStreaming](https://github.com/ufal/whisper_streaming) de segment trimming ile sentence trimming’i ayırır. Bunların performans iddiaları bu uygulamanın ölçümleri olarak aktarılmıyor. Kullanıcının güncel kabul tercihi: küçük sözcük/noktalama/üslup farkları tolere edilir; belirgin anlam tersliği, tekrar, kopma ve senkron kayması kabul engelidir.


## Kullanılabilir akışa odaklanan devam

Kullanıcının güncel kabul tercihi, yüksek genel doğruluk ve anlaşılır senkron oynatımdır; küçük sözcük, noktalama ve üslup farkları kabul engeli değildir. Quality bütçesini 24 s’ye çıkaran gerçek VOD denemesi `6836aa67aef245b1841b4e5ac1a87a51` yine iki oynatım sonrasında uzun kaynakta durdu. Bu süre artışı geri alındı; yeni sınırlar belirsizlik süresini körlemesine genişletmez.

Birleşik kaynak-sınırı+çeviri üretimi, kayıtlı ASR’de following-context sözcüklerini hedefe taşıyabildiği için alınmadı. Ayrı bir sınır seçimi ve ardından yalnız seçilmiş kaynağın çevirisi, tek sıcak Qwen üzerinde 24.294 s’de gerçek olarak denendi: dört kayıtlı VOD ASR snapshot’ı ve DE/TR/ZH/AR açık metin kontrolleri. İlk uzun gerçek kaynakta 14. birimde “birkaç düşüncemi paylaşmak” parçası seçildi; devamındaki arkadaş adı hedefe taşınmadı. Çince koşul parçası “If it rains tomorrow,” şeklinde kalır; bu bir sentence-completeness başarısı değildir, devamı ayrı parçada çevrilebilen faithful simultaneous fragment olarak değerlendirilir. Model yine semantik sertifika değildir.

İsteğe bağlı Qwen Live backend’i artık sınır **önerisi** verebilir. Ortak controller yalnız iki gerçek ASR penceresinde korunmuş kaynak prefix’ini, gerçek sağ bağlam/lookahead ve bölünmez timestamp gruplarıyla kabul eder. Öneri bir sonraki gerçek ASR gözleminde aynı kaynak kalmazsa iptal edilir; hedef çeviride birebir metin eşitliği aranmaz. Çeviri ve ses QA’sı ayrı yapılır. Varsayılan Hy-MT2, offline pipeline ve native Vox akışı değiştirilmedi. 168/168 unit test 6.609 s’de geçti; bu sayı gerçek tarayıcı kabulünün yerine kullanılmaz. Sonraki browser sonucu ayrıca kaydedilir.


## 27 Ekim: gerçek oynatma, hafif hata toleransı ve throughput

Küçük dolgu/noktalama farkları genel kabul engeli sayılmıyor. Aynı dilde intended TTS / bağımsız ASR çiftine dar Qwen karşılaştırması yapılır; orijinal WER/CER kalır. Ham 12 çiftte model 11/12, büyük tekrar korumasıyla birleşik politika 12/12; bu sınırlı kontrol tüm dillerde semantik doğruluk kanıtı değildir. İki başarısız ses adayından sonra açık yazılı sunum kullanılır ve sonraki native parçaya devam edilir.

Gerçek YouTube VOD: 19 native/19 gerçek played; TTFA p50 .1185 s, RTF .6264, mutlak A/V p95 .1458 s. Gerçek LIVE: 16 sesli + 3 yazılı gerçek played; yazıdan sonraki native ses geri geldi, A/V max .139 s, Pause/Resume ve orijinal/çevrilmiş ses seçimi gerçek tarayıcıda doğrulandı. LIVE daha sonra phrase_queue_overrun ile durdu; sürekli kabul geçmedi. Gecikmeler VOD p50 19.505 s, LIVE 7.997 s; model TTFA ile toplam kullanıcı gecikmesi karıştırılmıyor.

TTS hazırlanırken kaynak karelerinin erken silinmesi ayrı bir gerçek senkron kusuruydu. Her pending phrase kendi kaynak tutma kaydını alır; seek/append sırasında prune yapılmaz. Bu düzeltmeden sonraki LIVE'da önceki seek timeout tekrarlanmadı. Yalnız unit testiyle düzelmiş sayılmadı.

Üretim meşgulken 1–2 s'lik çok sayıda küçük parçanın sabit MT/QA maliyeti kuyruğu büyütüyordu. Moda göre 3/4/6 s gerçek kaynak partileri yalnız geçerli learned/contextual sınırda seçilir; kısa sessiz yanıtlar yine boşaltılır. Kaynak veya anlam zorla kırpılmaz, kuyruk sınırı artırılmaz. Sınır modelinin geçersiz önerisi bekle olarak kaydedilir; CUDA/worker arızası gizlenmez. Capture bittikten sonraki sonlu boşaltma canlı backpressure'dan ayrılır ve bounded bekleme kullanır.

Aynı SHA256 `f44efacc81f0aca69519d6d7eef3a8bcb21c2386dde1017cbafa666796b88967` kaynak PCM'nin 24.15 s'si üç modda gerçek 20 ms wall-clock paketlerle beslendi. Quality 6 parça /39.601 s, Balanced 8 /40.896 s, Low Latency 9 /34.467 s native çıktı; hepsinde tam kaynak işleme ve sıfır tail. Son Low Latency başarısız olgunun düzeltme kohortudur; bütün kohortların runtime guard sürümü aynı değildir. Üç modun tamamı tek nihai sürümde tekrar koşulmuş gibi sunulmaz. Süreler finite drain içerir, sürekli realtime throughput değildir; playback ACK yoktur.

40 s yetkili iki-konuşmacılı dosya tekrarında 11 native + 3 caption, 66.947 s toplam, sıfır failure/tail vardı. Yalnız LIVE_SPEAKER_00 ve UNCONFIRMED gözlendi, dört mixed-turn/overlap fallback oluştu. Bu, sağlam iki kimlik sürekliliğinin kanıtı değildir. Güncel rapor `temp/live-validation-20261026/CURRENT_VALIDATION_REPORT_TR.md`; önceki negatif sonuçlar tutulur.
