"""Create an illustrated walkthrough from screenshots of the deployed service."""
from pathlib import Path
from PIL import Image
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt
from tools import build_knowledge_pack as layout

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'output/screenshots/service-tour'
OUTPUT = ROOT / 'output/documents/cyber102-service-screenshots.docx'
BASE = 'https://cyber102.94.41.87.102.sslip.io/'


def start_page(doc, title, text, *, first=False):
    p = doc.add_paragraph(title, 'Title' if first else 'Heading 1')
    if not first:
        p.paragraph_format.page_break_before = True
    doc.add_paragraph(text)


def picture(doc, name, *, width=10.25, max_height=5.35, alt=''):
    path = ASSETS / name
    w, h = Image.open(path).size
    scale = min(width / w, max_height / h)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(7)
    p.paragraph_format.keep_with_next = True
    shape = p.add_run().add_picture(str(path), width=Inches(w * scale), height=Inches(h * scale))
    shape._inline.docPr.set('descr', alt or name)
    return p


def pair(doc, names, *, height=5.0, width=10.25, descriptions=None):
    sizes = [Image.open(ASSETS / name).size for name in names]
    aspects = [w / h for w, h in sizes]
    gap = 0.3
    height = min(height, (width - gap) / sum(aspects))
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.keep_with_next = True
    for i, (name, ratio) in enumerate(zip(names, aspects)):
        if i:
            p.add_run('   ')
        shape = p.add_run().add_picture(str(ASSETS / name), width=Inches(height * ratio), height=Inches(height))
        shape._inline.docPr.set('descr', descriptions[i] if descriptions else name)


def caption(doc, text, route, link_text='Открыть\u00a0экран'):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    p.add_run(text + ' ').font.size = Pt(10)
    layout.add_inline(p, [{'type':'link', 'href':BASE + route, 'tokens':[{'type':'text','text':link_text}]}], ROOT / 'TIMUR/README.md')
    for run in p.runs:
        run.font.size = Pt(10)


def main():
    doc = Document()
    layout.TEXT_WIDTH = 10.59
    layout.configure(doc)
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(11.69), Inches(8.27)
    section.left_margin = section.right_margin = Inches(0.55)
    section.top_margin = section.bottom_margin = Inches(0.50)
    section.footer_distance = Inches(0.20)
    footer = section.footer.paragraphs[0]
    footer.clear()
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run('Cyber102 · ')
    page_field = layout.xml('fldSimple', instr='PAGE')
    field_run = layout.xml('r')
    field_text = layout.xml('t')
    field_text.text = '1'
    field_run.append(field_text)
    page_field.append(field_run)
    footer._p.append(page_field)
    doc.styles['Title'].font.size = Pt(23)
    doc.styles['Title'].paragraph_format.space_after = Pt(6)
    doc.styles['Heading 1'].font.size = Pt(21)
    doc.styles['Heading 1'].paragraph_format.space_before = Pt(0)
    doc.styles['Heading 1'].paragraph_format.space_after = Pt(6)
    normal = doc.styles['Normal'].paragraph_format
    normal.line_spacing = 1.08
    normal.space_after = Pt(6)
    doc.core_properties.title = 'Cyber102 на экране'
    doc.core_properties.subject = 'Скриншоты работающего сервиса для первого чекпоинта'
    doc.core_properties.author = 'Команда Cyber102'

    start_page(doc, 'Cyber102 на экране',
        'Скриншоты стенда на 13 ноября 2026 года показывают путь от осмотра к паспорту, показаниям и объяснению правил. '
        'Материал предназначен для команды и первого чекпоинта. Подписи указывают, какие данные использует каждый экран.', first=True)
    picture(doc, '01-camera-demo.png', width=8.75, max_height=5.42,
            alt='Кадр промышленного насоса с рамкой вокруг фланца и карточкой тестового результата')
    caption(doc, 'Осмотр оборудования. На экране встроенный тестовый ответ: рамка, карточка узла и рекомендация. Сроки службы и ТО приведены как примеры.', 'index.html')

    start_page(doc, 'Осмотр в мобильном интерфейсе',
        'На узком экране кадр и результат располагаются друг за другом. Крупные кнопки позволяют включить камеру и проверить снимок. '
        'Этот общий веб-интерфейс используется в Safari и Android WebView.')
    pair(doc, ['01b-camera-mobile.png', '01c-result-mobile.png'], height=5.05,
         descriptions=['Мобильный вид кадра и кнопок осмотра', 'Мобильная карточка тестового результата'])
    caption(doc, 'Слева кадр, справа карточка того же тестового сценария. Снимки сделаны при мобильной ширине браузера; это не проверка готового APK на физическом телефоне.', 'index.html')

    start_page(doc, 'Ответ модели на фотографии',
        'InternVL3 на сервере команды выделила электродвигатель и сообщила о возможных признаках коррозии. '
        'Здесь показан настоящий ответ модели на иллюстративном изображении из подборки организаторов.')
    picture(doc, '08a-vision-image.png', width=6.40, max_height=3.70,
            alt='Изображение насосного агрегата с водяным знаком Qwen и рамкой модели вокруг электродвигателя')
    picture(doc, '08b-vision-answer.png', width=6.40, max_height=1.80,
            alt='Реальный предварительный ответ InternVL3 о возможных признаках коррозии электродвигателя')
    caption(doc, 'На показе: выбрать снимок в материалах и нажать «Проверить фото». Наблюдение модели требует подтверждения инженером.', 'materials.html#photos')

    start_page(doc, 'Каталог паспортов оборудования',
        'В библиотеке доступны 22 PDF и 16 паспортных записей: насосы AMS и связанные компоненты. '
        'На фрагменте экрана показаны первые три насоса; серийные номера помогают различать агрегаты.')
    picture(doc, '02b-passports-catalog.png', width=10.25, max_height=4.10,
            alt='Три карточки насосов AMS с моделями, серийными номерами и изготовителем')
    doc.add_paragraph('В раскрытой карточке ссылки ведут к страницам оригинала. Например, серийный номер насоса №1 связан со страницей 1 его паспорта.')
    picture(doc, '03c-serial-source.png', width=6.20, max_height=0.65,
            alt='Ссылка на страницу 1 паспорта и серийный номер 21031004-1')
    caption(doc, 'На показе: открыть «Характеристики и источники» и перейти к странице PDF. Паспорта AMS учтены отдельно от демонстрационного P-102.', 'documents.html')

    start_page(doc, 'Характеристики выбранного насоса',
        'Раскрытая карточка насоса №1 содержит назначение, допустимые режимы, габариты и паспортные значения. '
        'Параметры можно сверить с исходным PDF по ссылкам в той же карточке.')
    picture(doc, '03a-passport-values.png', width=6.35, max_height=5.36,
            alt='Паспортные характеристики винтового насоса AMS включая год 2023 и паспортный срок службы 20 лет')
    caption(doc, '20 лет и ресурс не менее 11 000 часов — сведения из паспорта. Это не расчёт оставшегося срока эксплуатации конкретного агрегата.', 'documents.html')

    start_page(doc, 'Текущая строка телеметрии',
        'Эмулятор воспроизводит 500 почасовых строк из таблиц организаторов. Экран разделяет время исходного измерения и время получения с сервера, '
        'показывает значения каналов и оценку по заданным порогам.')
    picture(doc, '04c-telemetry-overview.png', width=10.25, max_height=5.10,
            alt='Время измерения и шесть карточек каналов вибрации и температуры из эмулятора')
    caption(doc, 'На показе: нажать «Следующая строка» или «Воспроизвести». Для общей вибрации порог не задан, а связь таблицы с серийным номером ещё не подтверждена.', 'telemetry.html')

    start_page(doc, 'Графики температуры и вибрации',
        'В материалах доступны графики и выбор измерительного канала. Подписи сохраняют названия исходных файлов, уставки и найденные расхождения меток, '
        'чтобы вывод можно было проверить по переданным данным.')
    picture(doc, '07-measurements.png', width=8.40, max_height=5.42,
            alt='Графики вибрации и температуры из таблиц организаторов с описанием порогов')
    caption(doc, 'На показе: сменить канал и объяснить изменение графика. Это пример набора данных; в нём отсутствуют ток, давление, КПД и наработка.', 'materials.html#measurements')

    start_page(doc, 'Правила и выделение узла на схеме',
        'В подготовленном сценарии «Кожух, утечка и регламентное ТО» сработали четыре правила. '
        'Открытая карточка объясняет нарушение защиты муфты, а схема выделяет соответствующий узел.')
    pair(doc, ['05a-diagram.png', '05b-findings.png'], height=3.60,
         descriptions=['Схема насоса с выделенной защитой муфты', 'Четыре срабатывания правил с раскрытым объяснением отсутствующего или повреждённого кожуха'])
    doc.add_paragraph('На показе: выбрать сценарий, нажать «Проверить» и раскрыть нужный вывод. '
                      'Система показывает основание и рекомендацию. Отсутствие кожуха здесь задано входом сценария; этот экран не подтверждает его автоматическое распознавание камерой.')
    caption(doc, 'Демонстрационный центробежный насос P-102. Применимость правил и уставок к винтовым AMS требует отдельной инженерной проверки.', 'checkpoint.html?serial=P-102')

    start_page(doc, 'Журнал демонстрационных осмотров',
        'Сохранённая запись содержит время, сценарий, заключения и заметку. На снимке видны записи проверки развёртывания в Chromium и WebKit. '
        'Они сохраняются на сервере и доступны после обновления страницы.')
    picture(doc, '06-history.png', width=7.40, max_height=4.60,
            alt='История двух демонстрационных осмотров с критическим выводом по защитному кожуху')
    doc.add_paragraph('На показе: добавить заметку, нажать «Сохранить осмотр», обновить страницу и найти запись. '
                      'Следующий этап — единый реальный осмотр с привязкой кадра, подтверждённых признаков и показаний к выбранному агрегату.')
    caption(doc, 'Маршрут для чекпоинта: камера → паспорт → телеметрия → правило → журнал. Сейчас разделы демонстрируют работающие части этого процесса.', 'checkpoint.html?serial=P-102')

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == '__main__':
    main()
